"""失败话术翻译层 —— **机器看到的** 和 **达人看到的** 分开。

★ 为什么需要这一层（2026-10-25 老大反馈）：
  老大说「浏览器的掉了，我也不知道，就是需要我重新扫码登陆或者验证码登陆，
  我都不知道」—— 达人收到的原文是：

    任务失败：RuntimeError: 达人端采集账号登录态过期了，跟运营老师说一声扫码重登就好
              （已返还 20 积分，说「重新采集」可重试）

  三个问题：
    ① **技术异常名**（`RuntimeError:`）对达人毫无意义
    ② **话术是写给运营的**（「跟运营老师说一声」）⇒ 达人不知道该干嘛
    ③ **达人不知道这是要他做什么**（扫码？验证码？找谁？等多久？）

⇒ 这一层负责把异常翻译成**达人看得懂 + 能立刻行动**的话，
  同时把**机器细节**（异常类型、原始消息）留给日志/运营看。
  ⇒ 原则：**对外只说「要做什么」，不泄漏「怎么实现的」**。

★★ 达人话术里**不许出现**的东西（有一道静态闸门盯着，见 tests/test_failure_text.py）：
   - 异常类名（RuntimeError / NeedLogin / ProductNotFound …）
   - 端口号 / 内部路径 / 内部服务名
   - **我们的内部状态**（「采集账号登录过期」「需要扫码」）——
     ★ 这是**我们的问题**，不是达人的问题。达人只需要知道：
       「现在拿不到 + 我记下了 + 你可以怎么做」。
     达人不需要知道要去扫码，更不需要去联系谁。

★★ 两个维度，别混：
   - **失败类型**：need_login / not_found / collector_down / timeout / other
   - **任务场景**：product（商品/图片截图分析）/ generic（采集账号、周报、问答…）
     ⇒ 「发商品截图我照样能分析」这句**只对商品场景成立**，
       塞给「采集对标账号」的任务就是驴唇不对马嘴。
"""
from __future__ import annotations

# ── 达人可见话术：商品场景 ──
# 每条都必须是：① 说清现在什么情况 ② 说清达人要做什么 ③ 给个替代方案
# ⚠️ 不许出现：异常类名、端口号、内部路径、给运营看的指示、内部状态词。
#
# ★ 「积分已退」**不在这里说** —— 由 worker 统一追加（`（已返还 N 积分）`），
#   免得两处各说一次、金额还不一致。
_USER_TEXT_PRODUCT = {
    "need_login": (
        "这个商品的数据我暂时没取到（我们这边的取数通道在维护）。\n"
        "我已经记下来了，等通道恢复你再发一次这个链接就行～\n"
        "着急的话，直接发商品截图给我，我照样能分析。"
    ),
    "not_found": (
        "这个商品在平台上已经查不到数据了（可能已下架，或不在当前推广池里）。\n"
        "发个商品截图给我吧，截图我一样能分析～"
    ),
    "collector_down": (
        "商品数据服务现在连不上，我这边正在排查。\n"
        "等会儿再发一次这个链接就行～\n"
        "或者直接发商品截图，我照样能分析。"
    ),
    "timeout": (
        "这次查商品数据超时了（平台那边响应慢）。\n"
        "过几分钟再发一次就行～\n"
        "或者直接发商品截图，我照样能分析。"
    ),
}

# ── 达人可见话术：通用场景（采集对标号 / 周报 / 问答…）──
# ★ 不提「商品」「截图」—— 那些在别的场景没有意义。
_USER_TEXT_GENERIC = {
    "need_login": (
        "这个我暂时没跑出来（我们这边的取数通道在维护）。\n"
        "我已经记下来了，等通道恢复你再发一次就行～"
    ),
    "not_found": (
        "这个目标现在已经查不到数据了。\n"
        "换个目标再发一次就行～"
    ),
    "collector_down": (
        "服务现在连不上，我这边正在排查。\n"
        "等会儿再发一次就行～"
    ),
    "timeout": (
        "这次超时了（平台那边响应慢）。\n"
        "过几分钟再发一次就行～"
    ),
}

# 兜底：任何没归类的失败
_FALLBACK_PRODUCT = (
    "这次没查成功，我再试一次就好～\n"
    "也可以直接发商品截图给我，我照样能分析。"
)
_FALLBACK_GENERIC = (
    "这次没跑成功，我再试一次就好～\n"
    "过几分钟再发一次就行。"
)


def classify(exc: BaseException) -> str:
    """把异常归类成一个 key（need_login / not_found / collector_down / timeout / other）。

    ★ 认**类名 + 消息特征**，不精确 import —— 避免 worker ↔ executors 的循环依赖，
      也避免测试里 monkeypatch 类之后判定失效。
    """
    if _is_need_login(exc):
        return "need_login"
    if _is_not_found(exc):
        return "not_found"
    if _is_collector_down(exc):
        return "collector_down"
    if _is_timeout(exc):
        return "timeout"
    return "other"


def user_text_for(exc: BaseException, *, product: bool = True) -> str:
    """把异常翻成达人看得懂的话。

    ★ 达人看到的**只有**这段话；技术细节留在日志里。
    ★ `product=True` 走商品场景话术；`False` 走通用场景。
    """
    key = classify(exc)
    table = _USER_TEXT_PRODUCT if product else _USER_TEXT_GENERIC
    return table.get(key, _FALLBACK_PRODUCT if product else _FALLBACK_GENERIC)


def is_login_failure(exc: BaseException) -> bool:
    """是不是「采集通道登录态失效」—— 运营需要被叫醒的那一类。"""
    return classify(exc) == "need_login"


def _is_need_login(exc: BaseException) -> bool:
    """登录态失效的判定。

    ★ 认**类名**而不是精确 import —— 避免 worker ↔ executors 的循环依赖，
    也避免测试里 monkeypatch 类之后判定失效。
    """
    if type(exc).__name__ == "NeedLogin":
        return True
    msg = str(exc)
    return ("登录态过期" in msg or "need_login" in msg
            or "扫码重登" in msg or "重新扫码" in msg)


def _is_not_found(exc: BaseException) -> bool:
    if type(exc).__name__ == "ProductNotFound":
        return True
    msg = str(exc)
    return ("查不到这个商品" in msg or "已下架" in msg
            or "不在当前推广池" in msg)


def _is_collector_down(exc: BaseException) -> bool:
    msg = str(exc)
    return ("采集器未启动" in msg or "不可达" in msg)


def _is_timeout(exc: BaseException) -> bool:
    msg = str(exc)
    return ("超时" in msg or "timed out" in msg.lower()
            or "TimeoutError" in type(exc).__name__)


def ops_text_for(exc: BaseException) -> str:
    """给**运营**看的：保留技术细节，好定位。

    ★ 这是跟达人那条**分开的** —— 运营要知道是达人端还是团长端掉登录、
      原始异常是什么。
    """
    side = getattr(exc, "side", "") or ""
    detail = f"{type(exc).__name__}: {exc}"
    if side:
        return f"[{side}登录态失效] {detail}"
    return detail


def ops_alert_body(exc: BaseException, *, context: str = "") -> str:
    """给运营的**告警正文**：说清「发生了什么」+「该做什么动作」。

    ★ 运营（老大）要的不是异常栈，是**一个动作**：
      「达人端掉登录了 → 去采集器那个浏览器里扫码登录达人工作台」。
    """
    side = getattr(exc, "side", "") or ""
    who = side or "采集账号"
    lines = [
        f"采集通道登录态失效（{who}）：{exc}",
        "动作：打开采集器的浏览器窗口，扫码登录「达人工作台」；登录好之后商品查询自动恢复。",
    ]
    if context:
        lines.insert(1, f"触发场景：{context}")
    return "\n".join(lines)
