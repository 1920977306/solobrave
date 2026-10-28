"""异步任务 worker —— 重活全在这里（spec 3 秒硬约束的反面）。

内测接入顺序：douyin（已接 buyin_worker 管道）→ product → image（vision）。
未接入的类型返回 NotImplementedError，由 Worker 统一走
「任务失败 → 自动返还积分 → 卡片告知」路径（spec 4.1 失败分支）。
"""
from __future__ import annotations
import json
import logging
import threading
import time

from . import db
from . import executors
from . import failure_text
from . import ops_alert

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
        last_purge = 0.0
        last_reclaim = 0.0
        while not self._stop_event.is_set():
            # ★ 2026-10-28：僵死回收从「启动时一次」改为「每分钟巡检」——
            #   重启竞态（旧进程被 kill 的瞬间认领了任务 ⇒ 任务卡 collecting
            #   永远不再被领）靠启动时回收盖不住：新进程的回收跑在旧进程
            #   死前认领**之后**。30 分钟门槛（claimed_at 口径）远长于任何
            #   正常任务 ⇒ 周期回收不会抢正在跑的任务。
            if time.time() - last_reclaim > 60:
                last_reclaim = time.time()
                try:
                    n = db.reclaim_stale_collecting()
                    if n:
                        log.warning("reclaimed %d stale collecting task(s) -> pending", n)
                except Exception:
                    log.exception("reclaim stale collecting failed")
            # feat/daren-reflow 管道 3: 每小时滚动清一次 30 天前老 payload (行保留只清内容)
            if time.time() - last_purge > 3600:
                last_purge = time.time()
                try:
                    purged = db.purge_old_payloads()
                    if purged:
                        log.info("purged %d stale task payloads (30d)", purged)
                except Exception:
                    log.exception("purge_old_payloads failed")
            task = db.claim_next_task()
            if not task:
                self._stop_event.wait(self.poll_interval)
                continue
            try:
                self._process(task)
            except Exception:
                # ⚠️ 单条任务失败（含飞书推送失败）**不能让 worker 线程猝死** ——
                #   线程一死，整个机器人静默停摆：日志里只有一条 traceback，
                #   之后所有任务都不再处理（达人也等不到任何回复），而且没人发现。
                #   单条任务失败 ≠ worker 生命周期结束 → 记日志，继续跑下一条。
                log.exception("process task failed (worker continues): id=%s type=%s",
                              task.get("id"), task.get("type"))

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
            self._fail(task, str(e), exc=e)
        except Exception as e:
            log.exception("task failed: id=%s", task["id"])
            self._fail(task, f"{type(e).__name__}: {e}", exc=e)
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
                # feat/daren-reflow: 数据回流三管道 (落 payload + 达人库 upsert + 知识库洞察)
                # 失败只记日志, 不影响主流程
                try:
                    from . import reflow
                    reflow.after_douyin_collect(task)
                except Exception:
                    log.exception("reflow failed: %s", task["id"])
            if task["type"] == "douyin" and task.get("chat_id"):
                # 报告推送：优先可视化长图卡片，渲染失败回退纯文字
                from .executors import notify_report
                notify_report(self.wm.get_sender(tk), task["chat_id"],
                              task["target"], result_summary)
            elif task["type"] == "qa" and task.get("chat_id"):
                # 问答推送：优先可视化卡片，渲染失败回退纯文字
                executors.notify_qa_card(self.wm.get_sender(tk), task["chat_id"],
                                         task["target"], result_summary, tk)
                log.info("push ok: type=qa task=%s chat=%s",
                         task["id"], task["chat_id"])
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
                                              title, md, tk, stats, source,
                                              product_id=str(task.get("target") or ""))
                log.info("push ok: type=%s task=%s chat=%s cache_hit=%s",
                         task["type"], task["id"], task["chat_id"],
                         task.get("cache_hit"))
                # 看板「本周精选 TOP3」数据源：绿黄红结论落库（失败不阻塞主流程）
                # ⚠️ import 也要在 try 里 —— 渲染依赖（PIL）缺失时 import 就炸，
                #   放外面等于「一个可选的写表失败带累整个 worker 线程」。
                try:
                    from . import dashboard_card as _dc
                    _dc.save_product_verdict(tk, title, md)
                except Exception:
                    log.exception("save_product_verdict failed: task=%s", task["id"])
            elif task["type"] == "video" and task.get("chat_id"):
                # 视频拆解推送：优先分节卡片长图，渲染失败回退纯文本
                executors.notify_video_card(self.wm.get_sender(tk), task["chat_id"],
                                            result_summary, tk)
                log.info("push ok: type=video task=%s chat=%s",
                         task["id"], task["chat_id"])
            elif task["type"] == "weekly_report" and task.get("chat_id"):
                # 周报推送：长图卡片 + 摘要 + 文字版（OpenAPI，不走 ws）
                from . import weekly
                weekly.notify_weekly(self.wm.get_sender(tk), task["chat_id"],
                                     result_summary)
                log.info("push ok: type=weekly_report task=%s chat=%s",
                         task["id"], task["chat_id"])
            else:
                # qa/video 是对话型/报告型推送，不带「任务完成：」前缀
                prefix = "" if task["type"] in ("qa", "video") else "任务完成："
                self._notify(tk, f"{prefix}{result_summary}", task.get("chat_id", ""))

    def _fail(self, task: dict, reason: str, exc: BaseException | None = None) -> None:
        """任务失败：**达人看人话，运营看细节 + 收到告警**。

        ★ 2026-10-25 老大反馈：「浏览器的掉了，我也不知道」——
          之前这里直接把异常原文发给达人（`RuntimeError: 达人端采集账号登录态过期了，
          跟运营老师说一声扫码重登就好`）⇒ 达人看不懂、运营收不到。
          现在：**达人**走 `failure_text` 的话术层；**运营**走日志 + 告警通道。

        ★★ 2026-10-25 B 边（积分闸门对抗审查）：**退款改成走原子函数**
          `db.fail_task_and_refund()` —— 一个事务里「查幂等 → 标记 failed → 退款」。
          原来这里是 `mark_task(failed)` + `db.refund(...)` **两个独立写**：
            ① **没有幂等保护** ⇒ 同一任务 `_fail` 两次就**退两次钱**
               （worker 重启时 reclaim 把 collecting 打回 pending ⇒ 重跑又失败 ⇒ 双退；
                 `bot_credits_ledger` 又没有 `ref_task_id` 唯一约束 ⇒ DB 层拦不住）
               ⇒ **白送积分**（比多扣更糟：直接造钱）。
            ② 两个写不在一个事务 ⇒ 崩溃窗口「标记失败但没退款」
               ⇒ 达人的分**永久被扣**（failed 任务不会被 reclaim 捡回来重试）。
          ⇒ 幂等判据是**流水**（退过没有），不是任务状态。
        """
        cost = int(task.get("credits_cost") or 0)
        refunded, _balance = db.fail_task_and_refund(
            task["id"], reason, tenant_key=task.get("tenant_key", ""), cost=cost)

        # ★ 达人看到的**只有**人话；技术细节不外发
        product_scene = task.get("type") in ("product", "image")
        if exc is not None:
            user_text = failure_text.user_text_for(exc, product=product_scene)
            ops_text = failure_text.ops_text_for(exc)
        else:
            user_text = failure_text.user_text_for(RuntimeError(reason),
                                                    product=product_scene)
            ops_text = reason
        log.warning("[task-failed] id=%s type=%s tenant=%s refunded=%s cost=%s ops=%s",
                    task["id"], task.get("type"), task.get("tenant_key"),
                    refunded, cost, ops_text)

        # ★ 登录态掉了 ⇒ 运营必须知道（达人看到的「查不到数据」根因就在这）
        if exc is not None and failure_text.is_login_failure(exc):
            ops_alert.alert(
                "⚠️ 达人端采集通道掉登录了",
                failure_text.ops_alert_body(
                    exc, context=f"任务 {task['id']}（{task.get('type')}）失败"),
                kind="login_lost",
                dedup_key="daren_collector_need_login")

        suffix = f"（已返还 {cost} 积分）" if cost > 0 else ""
        self._notify(task["tenant_key"], f"{user_text}{suffix}",
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
