"""积分闸门 —— **扣费入口的唯一真源**。

★ 老大 2026-10-25：「每一个功能都需要积分控制 毕竟是消耗算力 不消耗的就算了」
  ⇒ 凡是**消耗算力**的路径（采集 / LLM 判分 / 视觉 / 视频转写 / 写报告）
    一律走本模块的 `charge_and_enqueue()`；
  ⇒ **不消耗算力**的功能（纯查库 + 纯函数：排品表查询 / 选品推荐 / 接品登记 /
    余额查询 / 订阅开关）**不扣分**，也不进 `config.COST_TABLE`
    （塞进表里 = 给白干的路开闸门）。

★ 为什么要统一闸门，而不是各入口各写一遍 `db.deduct`：
  ① **会漏**：分散写必然漏 —— 2026-10-25 体检就查出**周报生成**、
     **周报自动采集**、**采集失败后的重采** 三处一直在白干。
  ② **顺序错**：有的入口「先回确认消息、再扣分」⇒ 并发下余额被别人扣光时，
     达人收到「已开始采集」却**永远等不到报告**（真 bug，2026-10-25 修）。
  ③ **口径散**：动作名各处乱写 ⇒ 流水表里看不出「这分到底是干什么的」。
  ④ **不是原子**：入队是**幂等**的（同租户+同类型+同目标已有 pending ⇒ 返回旧任务、
     不新建）。若「扣费」与「入队」是两个独立调用 ⇒ 达人重复发一次就**扣两次钱、
     只建一个任务**；任务失败时只退一份 ⇒ **净多扣**（2026-10-25 实测：扣 200 / 只退 100）。

★ 所以本模块**只暴露一个扣费入口** `charge_and_enqueue()`：
  扣费与入队合并成**一个事务**（`db.enqueue_task_charged`），
  且**只有真建了新任务才扣分**（命中幂等 = 没多消耗算力 = 不收费）。
  ⚠️ 刻意**不提供**「只扣费不建任务」的裸接口 ——
     那正是「先扣费、再干活」两事务坑的源头（2026-10-25 踩过）。

★ 成本表在 `config.COST_TABLE`（常量唯一真源），本模块只管**怎么扣**。
"""
from __future__ import annotations

from . import config, db


def cost_of(action: str) -> int:
    """取某个计费动作的成本。

    ⚠️ **未登记的动作直接报错** —— 别让「忘了定价」悄悄变成「免费白干」。
    """
    try:
        return config.COST_TABLE[action]
    except KeyError:
        raise KeyError(
            f"未登记的计费动作 {action!r} —— "
            "消耗算力的功能必须先在 config.COST_TABLE 里定价"
        ) from None


def charge_and_enqueue(tenant_key: str, task_type: str, target: str,
                        action: str, cache_hit: int = 0,
                        chat_id: str = '', payload: str = '') -> tuple:
    """★ 原子「扣费 + 入队」—— **唯一的扣费入口**。

    返回 `(status, task_id, balance)`：
      status = "ok"           扣费成功 + 已入队
      status = "duplicate"    同目标已有在跑的任务 ⇒ **不扣费**（没多消耗算力）
      status = "insufficient" 余额不足 ⇒ **不扣费**、不入队

    ★ 为什么必须是「一个操作」：
      入队是**幂等**的（同租户+同类型+同目标已有 pending/collecting ⇒ 返回旧任务、
      不新建）。若「扣费」和「入队」是两个独立事务，达人重复发一次
      （同一个商品/同一个问题/同一个对标号）就会**扣两次钱、只建一个任务**；
      任务失败时 worker 只按任务上记的金额退一份 ⇒ **净多扣**。
      （2026-10-25 实测：扣 200、只有 1 个任务、只退 100。）
      ⇒ 现在先查幂等键、再查余额、最后「扣费+建任务」全在一个事务里，
        **只有真建了新任务才扣分**（老大：「不消耗的就算了」）。
    """
    cost = cost_of(action)
    return db.enqueue_task_charged(tenant_key, task_type, target, cost, action,
                                   cache_hit=cache_hit, chat_id=chat_id,
                                   payload=payload)


def enqueue_free(tenant_key: str, task_type: str, target: str,
                 action: str, cache_hit: int = 0,
                 chat_id: str = '', payload: str = '') -> str:
    """零成本入队（**不扣分**，但写一条流水留痕）。

    ★ 什么时候用：这次请求**没有消耗算力**（比如命中了问答缓存，
      根本就没调 LLM）⇒ 不该扣分，但流水里要留一条 `cache_hit=1` 的记录，
      方便日后对账「这批请求到底有多少是白捡的」。

    ⚠️ **不要**用它给「消耗了算力但忘了定价」的功能兜底 ——
       那是白干。消耗算力的路径一律走 `charge_and_enqueue()`。
    """
    # 记一笔 0 分流水（balance 不变），让「这次没花算力」在流水里可查
    db.deduct(tenant_key, 0, action, cache_hit=cache_hit)
    return db.enqueue_task(tenant_key, task_type, target, 0,
                            cache_hit=cache_hit, chat_id=chat_id, payload=payload)


def insufficient_text(action: str, balance: int) -> str:
    """余额不足的统一话术（同一份，别各写各的）。"""
    return (f"这个动作需要 {cost_of(action)} 积分，你的余额还剩 {balance} 分，不够啦。"
            "联系运营老师补充额度（内测期免费发放）")
