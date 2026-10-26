"""排品提醒（daren_bot/plan_notify.py）的测试。

用**临时库**（不碰生产 data/）跑完整链路：
登记接品 → 幂等 → 生成排期 → 生成今日提醒 → 提醒幂等。

★ 铁律锁点：
- 登记接品**幂等**（同达人同品不重复进池，否则坑位会被重复品占满）
- 排期/提醒**不写错数据**（只写达人明确登记过的品）
- 提醒**幂等**（同一天同一品只提醒一次）
"""
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

plan_notify = importlib.import_module("daren_bot.plan_notify")


DDL = """
CREATE TABLE talents (
    id TEXT PRIMARY KEY, name TEXT, douyin_id TEXT,
    category_distribution TEXT, price_distribution TEXT,
    average_price REAL, video_avg_price TEXT,
    total_products INTEGER, total_gmv REAL, created_at INTEGER,
    platform_talent_id TEXT, douyin_uid TEXT
);
CREATE TABLE products (
    id TEXT PRIMARY KEY, name TEXT, price REAL, price_range TEXT,
    category TEXT, commission_rates TEXT, tags TEXT,
    shop_score REAL, good_ratio REAL, video_sales_7d INTEGER,
    sales_7d INTEGER, board_rank INTEGER, sale_axis_json TEXT,
    platform_product_id TEXT, status TEXT
);
CREATE TABLE deals (
    id TEXT PRIMARY KEY, talent_id TEXT, product_id TEXT, product_name TEXT,
    deal_type TEXT, commission_rate REAL, status TEXT, scheduled_at INTEGER,
    actual_gmv REAL, actual_roi REAL, actual_units INTEGER, result_note TEXT,
    predicted_conclusion TEXT, predicted_event_id TEXT, verification TEXT,
    created_by TEXT, created_at INTEGER, updated_at INTEGER,
    win_loss_category TEXT, key_moment TEXT, decision_maker_feedback TEXT,
    priority REAL, plan_slot INTEGER, plan_reason TEXT,
    deal_source TEXT, remind_state TEXT
);
"""


@pytest.fixture()
def dbfile(tmp_path, monkeypatch):
    path = tmp_path / "sb.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(DDL)
    conn.execute(
        "INSERT INTO talents (id,name,douyin_id,category_distribution,"
        "price_distribution,average_price,video_avg_price,total_products,"
        "total_gmv,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("tal_x", "七月姐姐", "dy_888",
         json.dumps({"个护家清": 63, "美妆": 20}),
         json.dumps({"0~25": 100, "50~100": 400, "100~200": 600}),
         65.0, "¥65", 190, 500000, 1))
    conn.execute(
        "INSERT INTO products (id,name,price,category,commission_rates,"
        "shop_score,good_ratio,sale_axis_json,platform_product_id,status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("prod_a", "面霜A", 99, "个护家清/面霜",
         json.dumps({"default": 20}), 92, 95,
         json.dumps([{"date": f"202609{i:02d}", "qty": 30} for i in range(1, 15)]),
         "3668788850777522321", "active"))
    conn.execute(
        "INSERT INTO products (id,name,price,category,commission_rates,"
        "shop_score,good_ratio,sale_axis_json,platform_product_id,status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("prod_b", "面霜B", 199, "个护家清/面霜",
         json.dumps({"投放期": 25, "常规活动期": 5, "其他": 5}),
         90, 91,
         json.dumps([{"date": f"202609{i:02d}", "qty": 10} for i in range(1, 15)]),
         "3668788850777522322", "active"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(plan_notify, "SOLOBRAVE_DB", path)

    # ★ bot 自己的库也要隔离（2026-10-26）：
    #   `bot_plan_notice_log`（推送去重表）在 **bot 库** `daren_bot.db`，
    #   不在排品库 `solobrave.db` ⇒ 两个库都得指到 tmp，
    #   否则测试会往**生产库**写通知记录（违反 AGENTS.md §1 数据隔离）。
    bot_db = tmp_path / "daren_bot.db"
    monkeypatch.setattr(plan_notify.db.config, "DB_PATH", bot_db)
    plan_notify.db.init_db()
    return path


class _Ws:
    """假 WsManager（签名与真实 ws 一致：有 get_sender）。"""

    def __init__(self, sender_cls):
        self._sender_cls = sender_cls

    def get_sender(self, tenant_key):
        return self._sender_cls()


# ─────────────── 登记接品 ───────────────

def test_register_deal_by_product_id(dbfile):
    ok, msg = plan_notify.register_deal("dy_888", "3668788850777522321")
    assert ok is True
    assert "面霜A" in msg
    conn = sqlite3.connect(str(dbfile))
    row = conn.execute("SELECT * FROM deals").fetchone()
    cols = [c[0] for c in conn.execute("SELECT * FROM deals LIMIT 0").description]
    d = dict(zip(cols, row))
    conn.close()
    assert d["status"] == "approved"
    assert d["product_name"] == "面霜A"
    assert d["commission_rate"] == 20
    assert d["deal_source"] == "达人自选"


def test_register_deal_is_idempotent(dbfile):
    ok1, _ = plan_notify.register_deal("dy_888", "3668788850777522321")
    ok2, msg2 = plan_notify.register_deal("dy_888", "3668788850777522321")
    assert ok1 is True
    assert ok2 is False
    assert "已经登记" in msg2
    conn = sqlite3.connect(str(dbfile))
    n = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    conn.close()
    assert n == 1, "同一个品不能重复进池（否则坑位被重复品占满）"


def test_register_deal_unknown_product(dbfile):
    ok, msg = plan_notify.register_deal("dy_888", "9999999999999999999")
    assert ok is False
    assert "不在我们库里" in msg


def test_register_deal_without_talent(dbfile):
    ok, msg = plan_notify.register_deal("dy_unknown", "3668788850777522321")
    assert ok is False
    assert "抖音号" in msg


# ─────────────── 排期 ───────────────

def test_plan_text_lists_deals(dbfile):
    plan_notify.register_deal("dy_888", "3668788850777522321")
    plan_notify.register_deal("dy_888", "prod_b")
    text = plan_notify.plan_text("dy_888")
    assert "七月姐姐" in text
    assert "排品表" in text
    assert ("面霜A" in text) or ("面霜B" in text)


def test_plan_text_when_no_deals(dbfile):
    text = plan_notify.plan_text("dy_888")
    assert "还没有登记" in text
    assert "接了" in text          # 告诉达人怎么用


def test_plan_text_when_no_talent(dbfile):
    text = plan_notify.plan_text("dy_nobody")
    assert "抖音号" in text


# ─────────────── 今日提醒 ───────────────

def test_build_reminder_includes_reasons(dbfile):
    plan_notify.register_deal("dy_888", "3668788850777522321")
    text, todo = plan_notify.build_reminder("dy_888")
    assert "今天推" in text
    assert "面霜A" in text
    assert "佣金" in text
    assert todo, "应该有待标记的 deal"


def test_reminder_is_idempotent(dbfile):
    """★ 幂等要测**真实机制**（推送层），不是 build 层。

    幂等落在 DB 的 `remind_state` 上：
    第一次 scan_and_push 推出去并标记 → 第二次不再推。
    （只测 `build_reminder` 两次调用没有意义 —— 它不写标记，
      标记发生在**推送成功之后**，先标记再推会吞掉提醒。）
    """
    plan_notify.register_deal("dy_888", "3668788850777522321")
    sent = []

    class _Sender:
        def send_text(self, chat_id, text):
            sent.append((chat_id, text))

    monkey = plan_notify
    orig_gray = monkey._gray_map
    orig_chat = monkey.db.get_latest_chat_id
    monkey._gray_map = lambda: {"daren_boss": "dy_888"}
    monkey.db.get_latest_chat_id = lambda tk: "oc_test"
    try:
        # ★ 签名与 coop 一致：收 ws 对象（内部 get_sender）+ dry_run
        st1 = monkey.scan_and_push(_Ws(_Sender), dry_run=False)
        assert st1["pushed"] == 1, st1
        assert len(sent) == 1
        st2 = monkey.scan_and_push(_Ws(_Sender), dry_run=False)
        assert st2["pushed"] == 0, "同一天不能重复推送"
        assert len(sent) == 1, "同一天同一品只能提醒一次"
    finally:
        monkey._gray_map = orig_gray
        monkey.db.get_latest_chat_id = orig_chat


def test_reminder_idempotency_survives_restart(dbfile):
    """★ 机器人当天重启也不能重推（幂等不能只靠进程内存）。

    `PlanScheduler._last_day` 挂在内存里，重启就清空；
    所以幂等必须落在 DB 的 `remind_state` 上。
    """
    plan_notify.register_deal("dy_888", "3668788850777522321")
    sent = []

    class _Sender:
        def send_text(self, chat_id, text):
            sent.append((chat_id, text))

    orig_gray = plan_notify._gray_map
    orig_chat = plan_notify.db.get_latest_chat_id
    plan_notify._gray_map = lambda: {"daren_boss": "dy_888"}
    plan_notify.db.get_latest_chat_id = lambda tk: "oc_test"
    try:
        plan_notify.scan_and_push(_Ws(_Sender), dry_run=False)
        assert len(sent) == 1
        # 「重启」= 新进程，内存状态全丢
        plan_notify.scan_and_push(_Ws(_Sender), dry_run=False)
        assert len(sent) == 1, "重启后不能把今天的提醒重推一遍"
    finally:
        plan_notify._gray_map = orig_gray
        plan_notify.db.get_latest_chat_id = orig_chat


def test_register_deal_reads_phase_commission(dbfile):
    """★ 分档佣金结构也要读出来（收益维度不能被静默丢掉）。

    平台佣金有标量（{"default": 20}）和分档（{"投放期": 25, ...}）两种形态，
    只认标量会让商品的收益维度悄悄消失 —— 评分口径就和真值对不上了。
    """
    ok, msg = plan_notify.register_deal("dy_888", "prod_b")
    assert ok is True
    assert "25" in msg, msg
    conn = sqlite3.connect(str(dbfile))
    rate = conn.execute("SELECT commission_rate FROM deals").fetchone()[0]
    conn.close()
    assert rate == 25


# ─────────────── 排期落库（2026-10-24 实测根因） ───────────────

class _Rec:
    """记录推送内容的 sender。"""

    def __init__(self, sink):
        self._sink = sink

    def send_text(self, chat_id, text):
        self._sink.append((chat_id, text))

    def send_card(self, chat_id, card):
        """★ 2026-10-26：之前这个假 sender **没有** send_card ⇒
        每次推送都走「卡片失败 → 回退文字」的异常分支 ⇒
        **卡片这条主路径从来没被测过**（看着有覆盖，其实在测回退）。
        现在补上，卡片内容拍平成 JSON 存进 sink（断言「推了哪个品」照样能查）。
        """
        self._sink.append((chat_id, json.dumps(card, ensure_ascii=False)))


def _gray_one(monkey_holder, dyid="dy_888"):
    """把灰度名单临时改成只有一个租户，返回 (还原函数)。"""
    orig_gray = plan_notify._gray_map
    orig_chat = plan_notify.db.get_latest_chat_id
    plan_notify._gray_map = lambda: {"daren_boss": dyid}
    plan_notify.db.get_latest_chat_id = lambda tk: "oc_test"

    def _restore():
        plan_notify._gray_map = orig_gray
        plan_notify.db.get_latest_chat_id = orig_chat
    return _restore


def _deals_rows(dbfile):
    conn = sqlite3.connect(str(dbfile))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM deals ORDER BY created_at, id")]
    conn.close()
    return rows


def test_scan_persists_schedule_to_db(dbfile):
    """★ 排期必须**落库**（否则「安排」等于没做）。

    实测根因（2026-10-24）：排品算法有**两个消费者** ——
      ① 服务端 `POST /api/deals/plan`：落库
      ② 机器人 `scan_and_push`：只算不写
    只有 ① 落库 ⇒ 达人侧 `deals.scheduled_at` 永远是 0
    ⇒ 「已有排期保持原位」这条规则从来没生效过
    ⇒ 每天从头现算 ⇒ **明天会再推一遍一模一样的两个品**。
    """
    plan_notify.register_deal("dy_888", "3668788850777522321")
    sent = []
    restore = _gray_one(None)
    try:
        plan_notify.scan_and_push(_Ws(lambda: _Rec(sent)),
                                  dry_run=False)
    finally:
        restore()
    assert sent, "应该推了今天这一条"
    rows = _deals_rows(dbfile)
    assert rows[0]["scheduled_at"], \
        "排期必须写回 deals.scheduled_at（不落库 ⇒ 明天重复推同样的品）"
    assert rows[0]["scheduled_at"] < 4_000_000_000, "scheduled_at 单位是秒"
    # ★ 列序锁点：`priority` 和 `plan_slot` 是两列，写反了不会报错，
    #   只会让「坑位号」变成 58.6 这种分数（前端「第几个坑」直接显示错）。
    assert rows[0]["plan_slot"] in (1, 2), \
        f"plan_slot 应是坑位号(1/2)，实际 {rows[0]['plan_slot']}"
    assert rows[0]["priority"] and rows[0]["priority"] > 1, \
        f"priority 应是优先级分数，实际 {rows[0]['priority']}"


def test_dry_run_writes_nothing(dbfile):
    """★ 探针（dry_run）不能有任何副作用 —— 写库了就等于真推了。

    ⚠️ 基准是「**调用前**的状态」：登记接品本身会当场落排期
      （那句「帮你排进这周的品表」要成真），所以不能断言「整个流程都没写过」。
    """
    plan_notify.register_deal("dy_888", "3668788850777522321")
    before = _deals_rows(dbfile)[0]
    snapshot = (before["scheduled_at"], before["priority"], before["plan_slot"],
                before["remind_state"])
    restore = _gray_one(None)
    try:
        plan_notify.scan_and_push(None, dry_run=True)
    finally:
        restore()
    after = _deals_rows(dbfile)[0]
    assert (after["scheduled_at"], after["priority"], after["plan_slot"],
            after["remind_state"]) == snapshot, "dry_run 改动了库"


def test_schedule_is_stable_across_days(dbfile):
    """★ 核心回归：**明天不该再推今天推过的品**。

    这是老大手上「达人每天要接很多品 ⇒ AI 帮忙安排、提醒」的底线 ——
    安排必须固定，否则达人每天收到一模一样的两条推送，AI 排期等于没做。
    """
    import datetime
    plan_notify.register_deal("dy_888", "3668788850777522321")
    sent = []
    restore = _gray_one(None)
    try:
        plan_notify.scan_and_push(_Ws(lambda: _Rec(sent)),
                                  dry_run=False)
    finally:
        restore()
    assert len(sent) == 1

    # 明天再算一次排期
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    ms = int(datetime.datetime.combine(
        tomorrow, datetime.time.min).timestamp() * 1000)
    slots, _ = plan_notify.plan_for_talent("dy_888", start_at=ms)
    tstr = tomorrow.isoformat()
    again = [it for s in slots if s.date == tstr for it in s.items]
    assert not again, (
        "明天又排了同样的品 "
        f"{[i.product_name for i in again]} ⇒ 排期没落库，AI 安排是假的")


def test_overdue_unpushed_item_is_requeued(dbfile):
    """★ 漏推的品不能丢：排期已过 + 从没推过 ⇒ 退回重排（今天补推）。"""
    import datetime
    plan_notify.register_deal("dy_888", "3668788850777522321")
    yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
    ts = int(datetime.datetime.combine(yesterday, datetime.time.min).timestamp())
    conn = sqlite3.connect(str(dbfile))
    conn.execute("UPDATE deals SET scheduled_at=? WHERE talent_id='tal_x'", (ts,))
    conn.commit()
    conn.close()

    sent = []
    restore = _gray_one(None)
    try:
        plan_notify.scan_and_push(_Ws(lambda: _Rec(sent)),
                                  dry_run=False)
    finally:
        restore()
    assert len(sent) == 1, "排期过期且从没推过的品，应该今天补推一次"
    today_ts = int(datetime.datetime.combine(
        datetime.date.today(), datetime.time.min).timestamp())
    assert _deals_rows(dbfile)[0]["scheduled_at"] >= today_ts, \
        "补排后应排到今天（或之后的空坑），不能停在过去"


def test_pushed_item_is_not_requeued(dbfile):
    """★ 已经推过的（哪怕排期在昨天）**不重复推** —— 错挂比漏挂贵。

    ★ 2026-10-26（老大拍板「今天没排品也要发占位」后口径调整）：
      允许发**空态占位消息**（让达人知道系统今天确实没排，不是坏了），
      但**绝不许**把昨天推过的品再推一遍 —— 这条才是本用例要守的东西。
    """
    import datetime
    plan_notify.register_deal("dy_888", "3668788850777522321")
    yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
    ystr = yesterday.strftime("%Y-%m-%d")
    ts = int(datetime.datetime.combine(yesterday, datetime.time.min).timestamp())
    conn = sqlite3.connect(str(dbfile))
    conn.execute("UPDATE deals SET scheduled_at=?, remind_state=? "
                 "WHERE talent_id='tal_x'", (ts, ystr))
    conn.commit()
    conn.close()

    sent = []
    restore = _gray_one(None)
    try:
        plan_notify.scan_and_push(_Ws(lambda: _Rec(sent)),
                                  dry_run=False)
    finally:
        restore()
    for _chat, msg in sent:
        # ★ 真正要守的东西：昨天推过的**品**不许再推一遍（错挂比漏挂贵）
        assert "面霜A" not in msg, f"昨天已经推过的品被重推了: {msg}"
        # 空态占位允许发，但标题必须是「今天没排品」那版
        #（⚠️ 不能用 "今天推" not in msg 去判 —— 卡片备注里就有「今天推哪个」）
        if msg.startswith("{"):
            title = json.loads(msg)["header"]["title"]["content"]
            assert "没排品" in title, f"标题不对: {title}"


def test_plan_text_hides_past_days(dbfile):
    """★ 排品表不展示过去的日子（那是历史，不是待办）。"""
    import datetime
    plan_notify.register_deal("dy_888", "3668788850777522321")
    yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
    ystr = yesterday.strftime("%Y-%m-%d")
    ts = int(datetime.datetime.combine(yesterday, datetime.time.min).timestamp())
    conn = sqlite3.connect(str(dbfile))
    conn.execute("UPDATE deals SET scheduled_at=? WHERE talent_id='tal_x'", (ts,))
    conn.commit()
    conn.close()
    text = plan_notify.plan_text("dy_888")
    assert ystr not in text, f"排品表里不该出现过去的日子 {ystr}"


def test_build_reminder_empty_when_no_plan(dbfile):
    text, todo = plan_notify.build_reminder("dy_888")
    assert text == ""
    assert todo == []


# ─────────────── 文案必须跟着实际坑位数走（2026-10-24 实测） ───────────────

def test_reminder_title_matches_actual_count(dbfile):
    """★ 「今天推这两件」不能硬编码 —— 只有 1 个品时要说「这件」。

    实测：排品池只有 1 个品，提醒却写「今天推这两件」⇒ 达人一数就露馅。
    """
    plan_notify.register_deal("dy_888", "3668788850777522321")
    text, _ = plan_notify.build_reminder("dy_888")
    assert "今天推这件" in text, text
    assert "两件" not in text, text


def test_reminder_title_two_items(dbfile):
    plan_notify.register_deal("dy_888", "3668788850777522321")
    plan_notify.register_deal("dy_888", "prod_b")
    text, _ = plan_notify.build_reminder("dy_888")
    assert "今天推这两件" in text, text


def test_register_deal_persists_plan_immediately(dbfile):
    """★ 登记接品时那句「帮你排进这周的品表」必须**当场成真**。

    否则达人/运营马上查排品表是空的（排期要等下一次调度器扫描），
    声明端 ≠ 使用端。
    """
    plan_notify.register_deal("dy_888", "3668788850777522321")
    rows = _deals_rows(dbfile)
    assert rows[0]["scheduled_at"], "登记后应立刻把排期落库"
    assert rows[0]["plan_slot"] in (1, 2), rows[0]


# ─────────────── 「今天没排品」占位消息（2026-10-26 老大拍板）───────────────
#
# ★ 要守的三件事：
#   ① 「确实没品可推」⇒ **要有回音**（原来一条都不发，达人以为系统挂了）
#   ② 「我们没采到数据」⇒ **不许**说成「今天没排品」（把故障说成结论）
#   ③ 空态消息**一天只发一次**（没有 deal_id 挂不上 remind_state ⇒ 单独记标记位）


def test_no_pool_sends_empty_plan_placeholder(dbfile):
    """① 池里一张单都没有 ⇒ 发「今天没排品」占位（不是彻底静默）。"""
    sent = []
    restore = _gray_one(None)
    try:
        plan_notify.scan_and_push(_Ws(lambda: _Rec(sent)), dry_run=False)
    finally:
        restore()
    assert len(sent) == 1, f"没品可推时应有占位回音，实际发了 {len(sent)} 条"
    msg = sent[0][1]
    assert "没排品" in msg, msg


def test_empty_plan_placeholder_is_idempotent_within_the_day(dbfile):
    """③ 同一天重复扫 ⇒ 占位只发一次（DB 标记位，重启也守住）。"""
    sent = []
    restore = _gray_one(None)
    try:
        for _ in range(3):
            plan_notify.scan_and_push(_Ws(lambda: _Rec(sent)), dry_run=False)
    finally:
        restore()
    assert len(sent) == 1, f"同一天发了 {len(sent)} 条占位（幂等失效）"


def test_no_talent_does_not_send_placeholder(dbfile):
    """② 达人数据没采到 ⇒ **不发**占位（那是我们的故障，不是「没排品」）。"""
    sent = []
    restore = _gray_one(None, dyid="dy_ghost_no_such_talent")
    try:
        plan_notify.scan_and_push(_Ws(lambda: _Rec(sent)), dry_run=False)
    finally:
        restore()
    assert not sent, f"数据缺失时不该给达人发「今天没排品」: {sent}"


def test_empty_plan_placeholder_card_is_not_green(dbfile):
    """★ 卡片颜色不许误导：绿色模板的语义是「今天有好货」，空态用灰色。"""
    sent = []
    restore = _gray_one(None)
    try:
        plan_notify.scan_and_push(_Ws(lambda: _Rec(sent)), dry_run=False)
    finally:
        restore()
    assert sent, "应有占位消息"
    msg = sent[0][1]
    if msg.startswith("{"):
        card = json.loads(msg)
        assert card["header"]["template"] == "grey", \
            f"空态占位卡用了 {card['header']['template']} 模板（绿色=有好货，误导）"


def test_reminder_state_is_explicit(dbfile):
    """★ 状态必须是**显式枚举**（不许靠 text == '' 反推）。"""
    # 池空
    text, todo, state = plan_notify.build_reminder_state("dy_888")
    assert text == "" and todo == []
    assert state == plan_notify.PLAN_STATE_NO_POOL, state
    # 达人查不到
    text, todo, state = plan_notify.build_reminder_state("dy_ghost_no_such_talent")
    assert text == "" and todo == []
    assert state == plan_notify.PLAN_STATE_NO_TALENT, state
    # 有品可推
    plan_notify.register_deal("dy_888", "3668788850777522321")
    text, todo, state = plan_notify.build_reminder_state("dy_888")
    assert state == plan_notify.PLAN_STATE_OK, state
    assert text and todo, (text, todo)
