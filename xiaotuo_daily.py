#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小拓闹钟（修订2 2026-11-10 增量派单；班次加密 2026-11-18：每日 3 班）——
9:30 / 13:30 / 17:30 由 launchd 触发。

它只做一件事：把本班任务投进 emp_xiaotuo 的会话。活儿由小拓 agent 回路
自己干（tools/xiaotuo_ops.py scan → 自己判定 → decide → report 交日报，
流程见 workspace TOOLS.md）。

闹钟纪律：
- 不复述任务、不代写日报——「建议人工过目」必须由小拓自己的判定产生；
- 投完看门：CLI 等到 turn 结束，或网关已接受（turn 仍在跑）后，
  轮询 emp_duties.last_run_at 最多 45 分钟；到期没交日报 ⇒ 给管理员写告警；
- 任何一步失败都不许阻塞——闹钟响了就算尽到责，作业交没交看门说了算。

手动跑：python3 xiaotuo_daily.py
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'solobrave.db'
ADMIN_ID = 'user_7acb72ff'
AGENT_ID = 'emp_xiaotuo'
TURN_TIMEOUT = 2400        # agent 一整轮（扫描+判定+交付）CLI 最长等待 40 分钟
WATCHDOG_SEC = 45 * 60     # turn 早退（网关接受但 CLI 超时）后再等 45 分钟看作业
OPENCLAW_CLI = shutil.which('openclaw') or '/opt/homebrew/bin/openclaw'
# launchd 环境没有 homebrew 的 node：openclaw 启动脚本是 #!/usr/bin/env node，
# PATH 缺 node 会 127（2026-11-10 真跑实锤）⇒ 显式补齐 node 所在目录
_NODE_DIRS = [p for p in ('/opt/homebrew/bin', '/usr/local/bin')
              if (Path(p) / 'node').exists()]


def log(msg):
    print(f'[XiaotuoAlarm {datetime.now():%H:%M:%S}] {msg}', flush=True)


def _subprocess_env():
    env = os.environ.copy()
    env['PATH'] = ':'.join(_NODE_DIRS + [env.get('PATH', '')]).rstrip(':')
    return env


def load_duty():
    # 活库（WAL）只读一律普通 rw 句柄——mode=ro 可能建不了 -shm（AGENTS.md §16 Y）
    conn = sqlite3.connect(str(DB), timeout=10)
    try:
        row = conn.execute('SELECT * FROM emp_duties WHERE agent_id = ?',
                           (AGENT_ID,)).fetchone()
        cols = [d[0] for d in conn.execute(
            'SELECT * FROM emp_duties LIMIT 0').description]
        return dict(zip(cols, row)) if row else None
    finally:
        conn.close()


def build_task_message(duty, slot_label=None, class_no=None, class_total=None):
    """本班任务书：只投递，不解释。流程细节让 TOOLS.md 说了算。

    班次加密（2026-11-18）：带本班标识（第 N/3 班 · HH:MM），让日报/审计/
    B 表 run 能区分同一天的多班；模型侧也知道自己在跑哪一班。"""
    category = (duty.get('category') or '').strip() or '全部'
    quota = duty.get('quota') or 5
    cls_line = ''
    if slot_label:
        cls_line = (f'\n本班为今日第 {class_no}/{class_total} 班（{slot_label[:2]}:'
                    f'{slot_label[2:]} 班），流程与交付标准与平日一致。')
    return (
        f'开始今日达人拓展：类目={category}，配额 {quota}。{cls_line}\n'
        '流程（细节见 TOOLS.md）：\n'
        '1. tools/xiaotuo_ops.py scan 收候选；\n'
        '2. 逐个按判定三关给「排除/存疑/值得联系 + 一句话理由」，'
        '用 decide 落库（排除也记）；\n'
        '3. report 交日报，格式：今日深看N | 新增N | 排除N（主因） | '
        '建议人工过目：昵称（判定：理由）；……\n'
        '「建议人工过目」必须由你的真实判定产生，不许默认写「无」。\n'
        '★ 铁律（11-13 实证：fresh 会话 + k3 下 tool_search 现场发现交付工具'
        '空转 41 次、干完活没交卷）：收工必须按名字调用 tools/xiaotuo_ops.py '
        'report 交卷——上面三步的工具名都已点名，不允许靠 tool_search 现场'
        '发现；发现式交付等于赌博。\n'
        '★ 每日推荐 5 人（11-15 起，③号单）：日报正文必须有「每日推荐」段——'
        '从今日「值得联系」里选最多 5 人，每人一行：'
        '序号. 昵称（talent_id）｜一句话可验证理由（必须含数字，如结算额/粉丝数）。\n'
        '（系统会在你缺段时按 --rec 名单兜底组装该段——但那是兜底不是免检：'
        '正文自己写，验收对拍锚钉死这个段。）\n'
        '同时 report 命令带 --rec 落 B 表（talent_recommendations，同日去重），'
        'JSON 数组每项：{"talent_id","talent_name","matched_rules":[...],'
        '"reason_text","snapshot":{核心字段+判定依据字段},'
        '"insight":可选}。理由正文由系统从 snapshot 确定性组装（单一出口），'
        'reason_text 只作留痕、写不写都行；但如果你有「看出来的洞察」（如结算'
        '断崖/有粉没转化——复述字段以外的判断），必须放进 insight 字段，'
        '没有就省略、不许编。\n'
        'matched_rules 宁可空数组不可编造（没有命中的规则就存 []，编造的规则'
        '三个月后反推全是假规律）；snapshot 只放候选里有的字段。\n'
        '★ 反调研铁律（11-18 1730 班实锤：模型读 worker 源码/翻历史通知做'
        '「核实」，耗尽长度预算 → non_deliverable_terminal_turn 未交卷）：'
        'scan/decide 工具的输出即全部依据，禁止读源码、禁止翻历史通知/日志/'
        '台账做二次调研，拿到结果直接写日报交卷；存疑判据从简。'
    )


# 班次加密（2026-11-18 老大 18:29 拍板）：每日 3 班，launchd 三个日历触发点
# 与此表一一对应。稳定性口径同步：每天 3 个数据点，7 天观察期 = 21 个班。
CLASS_SLOTS = ((9, 30), (13, 30), (17, 30))


def class_slot(now=None):
    """当前时刻所属班次。返回 (slot_start_dt, label)。

    - now >= 某班次点 ⇒ 属于最近已过的那一班（09:30–13:29=0930 班，依此类推）；
    - 早于首班（清晨手动跑）⇒ slot_start=今日 00:00、label=当前 HHMM——
      保持旧「今日去重」语义，且每次清晨手动跑各得独立会话。
    """
    now = now or datetime.now()
    today_slots = [now.replace(hour=h, minute=m, second=0, microsecond=0)
                   for h, m in CLASS_SLOTS]
    for s in reversed(today_slots):
        if now >= s:
            return s, f'{s:%H%M}'
    return (now.replace(hour=0, minute=0, second=0, microsecond=0),
            f'{now:%H%M}')


def class_ordinal(slot_label):
    """班次序号（1 起）。清晨手动跑（label 不在排班表）返回 None。"""
    labels = [f'{h:02d}{m:02d}' for h, m in CLASS_SLOTS]
    return labels.index(slot_label) + 1 if slot_label in labels else None


def daily_session_key(slot_label=None):
    """每班新会话键（2026-11-12 首创：默认会话膨胀 → preflight compaction
    flaky 失败直接退出码 1，实测 9:30/11:22 两轮全灭；2026-11-18 班次加密
    修订为每班一键：daily-YYYYMMDD-HHMM）。每天/每班一个干净会话让 preflight
    永不触发；员工连续性由 workspace 的 SOUL.md/TOOLS.md 承载，不靠会话历史。
    同一天三班互不串会话——班级键不同，上一班的中断上下文不进下一班。
    """
    base = f'agent:{AGENT_ID}:daily-{datetime.now():%Y%m%d}'
    return f'{base}-{slot_label}' if slot_label else base


def _cli_terminal_state(output):
    """CLI --json 终态判读（投递语义 P2，dq_83e3e05ee394，2026-11-20 探针实样定字段）：
    {runId, status, summary, result:{...,meta:{stopReason,error,...}}}——
    健康只放行见过的形态：status=='ok' 且 stopReason ∈ {None,'stop','end_turn'} 且 error 空。
    白名单哲学：timeout/error/failed/length 截断（11-18 双病班签名）一律判终态异常。
    旧网关形态（无 status 且无 result.meta）→ None：无从判读，回落旧看门行为（向后兼容）。"""
    if not isinstance(output, dict):
        return None
    status = output.get('status')
    meta = (output.get('result') or {}).get('meta') or {}
    stop = meta.get('stopReason')
    err = meta.get('error')
    if status is None and not meta:
        return None
    if status == 'ok' and stop in (None, 'stop', 'end_turn') and not err:
        return {'healthy': True}
    return {'healthy': False, 'status': status, 'summary': output.get('summary'),
            'stop_reason': stop, 'error': (str(err)[:300] if err else None)}


def deliver_task(message, session_key=None):
    """投任务到 emp_xiaotuo 会话。返回 (exit_note, accepted_run_id, returncode, terminal)。

    2026-11-15（§165）：加 session_key 参数——exit 143 外部 SIGTERM 重试时
    必须换新会话键（同一退化会话重试会重蹈漫游，11-13 红线）。
    2026-11-20（投递语义 P2）：terminal = _cli_terminal_state(stdout JSON)——
    CLI 对终态 error turn 也返 rc=0（11-18 病班 45min 才发现的元凶），
    终态必须显式判读；无法判读（旧形态）为 None，回落旧看门。"""
    args = [OPENCLAW_CLI, 'agent', '--agent', AGENT_ID, '--message', message,
            '--session-key', session_key or daily_session_key(),
            '--json', '--timeout', str(TURN_TIMEOUT)]
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                encoding='utf-8', errors='replace',
                                timeout=TURN_TIMEOUT + 120, env=_subprocess_env())
    except subprocess.TimeoutExpired:
        return ('CLI 等待超时（turn 可能仍在跑）', None, None, None)
    except Exception as e:
        return (f'CLI 调用失败：{e}', None, None, None)
    if result.returncode != 0:
        return (f'CLI 退出码 {result.returncode}：{(result.stderr or "")[:200]}', None,
                result.returncode, None)
    try:
        output = json.loads(result.stdout)
    except Exception:
        return (f'CLI 输出非 JSON：{(result.stdout or "")[:200]}', None,
                result.returncode, None)
    terminal = _cli_terminal_state(output)
    run_id = output.get('runId') if isinstance(output, dict) else None
    if output.get('ok') is False and not run_id:
        return (f'网关拒绝：{json.dumps(output, ensure_ascii=False)[:200]}', None,
                result.returncode, terminal)
    note = ('turn 已结束' if output.get('ok') or (terminal or {}).get('healthy')
            else '网关已接受（turn 可能仍在跑）')
    return (note, run_id, result.returncode, terminal)


# 外部 SIGTERM 的退出码形态（11-14 09:37 实锤：CLI 被外部 kill → 优雅 chat.abort
# → exit 143；subprocess 原生形态为 returncode=-15。两种都认）。
EXTERNAL_KILL_RCS = (143, -15)


def report_delivered(since_ms):
    """看门：小拓在本次任务投递之后交作业了吗？（last_run_at 是 report 端点回写的）

    ⚠️ 不能判「今天」—— 同一天跑两轮时，第一轮的 last_run_at 也是今天，
    只看日期会让第二轮的看门狗立即误收工。必须拿投递时刻做下界。
    """
    conn = sqlite3.connect(str(DB), timeout=10)
    try:
        row = conn.execute('SELECT last_run_at FROM emp_duties WHERE agent_id = ?',
                           (AGENT_ID,)).fetchone()
        if not row or not row[0]:
            return False
        return row[0] >= since_ms
    finally:
        conn.close()


def _today_start_ms():
    """今天 00:00（本地时区）的毫秒时间戳。"""
    now = datetime.now()
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def already_reported_today(now=None):
    """当班去重闸（派单二缺陷1，2026-11-12；班次加密 2026-11-18 修订为
    「当班」语义）：本班是否已有成功日报。

    挡的是「同班手动重投」——18:43 实证：手动重投当日已完成的任务，小拓
    合理拒绝（「09:30 的班已跑完交过日报」），但那一轮规划 token 白烧了。
    下界 = 本班开班时刻：9:30 班交付后 13:30 班照常投（三班互不当误）；
    清晨手动跑（早于首班）保持旧「今日 00:00 起」语义。确需本班重跑，
    先清 emp_duties.last_run_at。
    """
    slot_start, _ = class_slot(now)
    lower = int(slot_start.timestamp() * 1000)
    duty = load_duty()
    last = (duty or {}).get('last_run_at') or 0
    return last >= lower


def report_notification_exists_today(since_ms=None):
    """看门狗复查（派单二缺陷2）：本班日报通知是否已落库。

    竞态实证（11-11 20:46）：日报通知 20:45:47 落库，看门狗 20:46:05（晚 18 秒）
    仍推「未交日报」误报——判「未交」时读到的是报告落库前的快照。
    ⇒ 告警落笔前必须双路复查：last_run_at + 本班日报通知，任一存在即静默跳过。
    班次加密（2026-11-18）：下界必须是本班投递时刻 since_ms——用「今日 00:00」
    会让 13:30 班看到 9:30 班的通知而误收工（跨班串判）。
    """
    lower = since_ms if since_ms is not None else _today_start_ms()
    try:
        conn = sqlite3.connect(str(DB), timeout=10)
        try:
            row = conn.execute(
                "SELECT 1 FROM notifications WHERE agent_id = ? AND title LIKE '%日报%' "
                "AND created_at >= ? LIMIT 1",
                (AGENT_ID, lower)).fetchone()
            return bool(row)
        finally:
            conn.close()
    except Exception as e:
        log(f'日报通知复查失败（按未存在处理，宁可多查）: {e}')
        return False


def alert_admin(note, run_id, slot_label=None):
    """小拓没交作业 ⇒ 管理员告警（旁路，失败只记日志）。

    班次加密（2026-11-18）：标题带本班标识——一天最多 3 条未交告警，
    班级可区分，也便于与当日日报通知对拍。"""
    cls = f' {slot_label[:2]}:{slot_label[2:]}班' if slot_label else ''
    try:
        now_ms = int(time.time() * 1000)
        conn = sqlite3.connect(str(DB), timeout=15)
        try:
            conn.execute(
                'INSERT INTO notifications (id, user_id, agent_id, type, title, '
                'content, read, created_at) VALUES (?,?,?,?,?,?,0,?)',
                ('notif_' + uuid.uuid4().hex[:12], ADMIN_ID, AGENT_ID,
                 'task_reminder', f'小拓未交日报 · {datetime.now():%m月%d日}{cls}',
                 f'闹钟已投任务（{note}' + (f'，runId={run_id}' if run_id else '')
                 + f'），45 分钟内未收到小拓的日报交付（{cls.strip() or "本班"}）。'
                   '请 @小拓 询问进度。',
                 now_ms))
            conn.commit()
        finally:
            conn.close()
        log('已写管理员告警（小拓未交日报）')
    except Exception as e:
        log(f'告警写入失败（不阻断）: {e}')


# 重试留痕（验收③「同班次标记 retried_from」，2026-11-15）：append-only JSONL，
# data/ 已 gitignore；通知标题里的「（重试班，retried_from=…）」是 DB 侧同锚点。
RETRY_LOG = ROOT / 'data' / 'xiaotuo_retry_log.jsonl'


def _log_retry(first_key, retry_key, first_rc, first_run_id):
    rec = {'ts': int(time.time() * 1000), 'agent': AGENT_ID, 'kind': 'sigterm-retry',
           'first_session_key': first_key, 'retry_session_key': retry_key,
           'first_rc': first_rc, 'first_run_id': first_run_id}
    try:
        with open(RETRY_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception as e:
        log(f'重试留痕写盘失败（不阻断）: {e}')


def mark_retry_delivery(first_key, since_ms):
    """重试班交付后给当日日报通知打「重试班」标（幂等，含 retried_from）。

    在 prompt 之外改通知标题（交付后落库即标），不依赖模型自觉注明——
    与验卷同哲学：检查在 AI 拦不住的地方。任何一步失败只记日志不阻断。"""
    try:
        conn = sqlite3.connect(str(DB), timeout=10)
        try:
            row = conn.execute(
                "SELECT id, title FROM notifications WHERE agent_id=? "
                "AND title LIKE '%日报%' AND title NOT LIKE '%未交%' "
                "AND title NOT LIKE '%重试班%' AND created_at>=? "
                "ORDER BY created_at DESC LIMIT 1",
                (AGENT_ID, since_ms)).fetchone()
            if not row:
                log('重试标注：未找到可标注的日报通知（按未标注处理，不阻断）')
                return
            nid, title = row
            conn.execute("UPDATE notifications SET title=? WHERE id=?",
                         (f'{title}（重试班，retried_from={first_key}）', nid))
            conn.commit()
            log(f'重试标注完成：{nid}')
        finally:
            conn.close()
    except Exception as e:
        log(f'重试标注失败（不阻断）: {e}')


def main():
    duty = load_duty()
    if not duty:
        log('emp_duties 没有小拓职责行，不投任务（先 PUT /api/agents/emp_xiaotuo/duties）')
        return 0
    # 本班标识（班次加密 2026-11-18）：会话键/任务书/告警/看门复查全挂它
    slot_start, slot_label = class_slot()
    class_no = class_ordinal(slot_label)
    # 当班去重闸（派单二缺陷1）：本班已交过日报 ⇒ 跳过手动重投，不白烧规划 token
    if already_reported_today():
        log(f'本班（{slot_label} 班）已交过日报（last_run_at 在本班开班时刻之后），'
            '跳过本次投递——如确需重跑，先清 emp_duties.last_run_at')
        return 0
    message = build_task_message(duty, slot_label=slot_label, class_no=class_no,
                                 class_total=len(CLASS_SLOTS))
    log(f'投递本班任务（{slot_label} 班）：类目={message.split("类目=")[1].split("，")[0]} '
        f'quota={duty.get("quota")}')

    dispatch_ms = int(time.time() * 1000)
    note, run_id, rc, terminal = deliver_task(message, session_key=daily_session_key(slot_label))
    log(f'投递结果：{note}' + (f'（runId={run_id}）' if run_id else ''))

    # ★ 外部 SIGTERM 自愈重试（§165，2026-11-15）：11-14 09:37 实锤——CLI 被外部
    #   kill（exit 143）后自请求 chat.abort 优雅中止，run 无病。发送者不可考
    #   （macOS 不记录信号来源），但修复不依赖它：立即换新会话键重试一次，
    #   而不是干等 45 分钟看门狗告警 = 白丢一班。仅一次防双烧；作业已交不重试。
    #   验收③（第四方背书 11-15）：retried_from 双锚点留痕（JSONL+通知标题），
    #   重试班看护不豁免（fall-through 进同一看门+外部熔断/验卷/积分看门）。
    retried = False
    retry_first_key = None
    if rc in EXTERNAL_KILL_RCS:
        if report_delivered(dispatch_ms) or \
                report_notification_exists_today(dispatch_ms):
            log('首投被外部中止（143），但本班作业已交付——无需重试，收工')
            return 0
        retry_first_key = daily_session_key(slot_label)
        retry_key = retry_first_key + '-r2'
        _log_retry(retry_first_key, retry_key, rc, run_id)
        log('首投 CLI 被外部 SIGTERM（exit 143，§165 实锤形态）——换新会话键重试一次')
        retry_msg = message + (
            '\n★ 本班为重试班：首投被外部信号中止（exit 143），流程照常跑完交日报。')
        note, run_id, rc, terminal = deliver_task(retry_msg, session_key=retry_key)
        retried = True
        log(f'重投结果：{note}' + (f'（runId={run_id}）' if run_id else ''))

    # ★ 投递语义 P2（dq_83e3e05ee394，2026-11-20）：CLI 终态直判病班。
    # 11-18 实证：terminalError turn 也返 rc=0，病班 19:54 死、20:39 才告警（45min 延迟）。
    # 探针实样定字段（顶层 status + result.meta.stopReason/error）；terminal=None
    # = 旧网关形态无从判读，回落下面看门（向后兼容）。直判只发即时告警，
    # 案卷仍由 run-watchdog 验卷层立（alert 是即时推送，立案幂等不重复）。
    if terminal and not terminal.get('healthy'):
        alert_admin(
            f'本班 turn 终态异常（投递语义直判，病班发现延迟 45min→<1min）：'
            f'status={terminal.get("status")} summary={terminal.get("summary")} '
            f'stopReason={terminal.get("stop_reason")} error={terminal.get("error")}；'
            f'note={note}（runId={run_id}，rc={rc}）',
            run_id, slot_label=slot_label)
        return 0

    # 看门：turn 结束后（或早退）等它交作业
    deadline = time.time() + WATCHDOG_SEC
    while time.time() < deadline:
        if report_delivered(dispatch_ms):
            if retried:
                mark_retry_delivery(retry_first_key, dispatch_ms)
            log('小拓已交日报（emp_duties.last_run_at 已在本次投递后回写），收工')
            return 0
        time.sleep(60)
    # 看门狗竞态复查（派单二缺陷2）：落笔告警前最后确认一次——
    # 日报可能恰在最后一次轮询后、告警前落库（11-11 20:45:47 落库 vs
    # 20:46:05 误报，只差 18 秒）。双路任一命中即静默跳过。
    # 班次加密（2026-11-18）：复查下界=本班投递时刻——上一班的通知不算本班交付。
    if report_delivered(dispatch_ms) or \
            report_notification_exists_today(dispatch_ms):
        if retried:
            mark_retry_delivery(retry_first_key, dispatch_ms)
        log('复查命中：日报已交付（竞态窗口内落库），告警静默跳过')
        return 0
    alert_admin(note, run_id, slot_label=slot_label)
    return 0


if __name__ == '__main__':
    sys.exit(main())
