#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小拓每日调度（A 简报 2026-11-10）—— 9:30 由 launchd 触发。

流程：
  1. 读 emp_duties 里小拓的职责行（category/quota；没有行就退出）
  2. POST buyin_worker :8095/square-scan → 轮询 /jobs/<id> 到终态（最长 30 分钟）
  3. 每个新入库候选人 → 过小拓自己的判定（openclaw CLI 走它的 SOUL.md /
     kimi_proxy_xiaotuo，积分走它自己的代理通道）——判定结论回写 talents.remark
  4. 按简报日报格式出报告：今日深看N | 新增N | 排除N（主因）| 建议人工过目：XXX
  5. 写 notifications（user_7acb72ff）+ 回写 emp_duties.last_run_*（名片「近期动态」）

手是手、脑是脑（验收必修② 2026-11-10）：机械查重在 worker 脚本层，
「排除|存疑|值得联系」三态判定由小拓（LLM）逐个过，判定通道任何异常都
落「存疑：判定通道异常，转人工过目」，绝不阻断日报。

铁律沿用工具层：风控/登录态失败如实报，绝不重试。
手动跑：python3 xiaotuo_daily.py
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib import request as _req

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'solobrave.db'
WORKER = 'http://127.0.0.1:8095'
ADMIN_ID = 'user_7acb72ff'
AGENT_ID = 'emp_xiaotuo'
POLL_SEC = 30
TIMEOUT_SEC = 30 * 60
JUDGE_TIMEOUT = 300          # 单个候选人判定超时（openclaw --timeout 同值；真跑实测一轮
                           # kimi 429 → minimax 兜底的完整 turn 需 150-240s，留足余量）
OPENCLAW_CLI = shutil.which('openclaw') or '/opt/homebrew/bin/openclaw'
# launchd 环境没有 homebrew 的 node：openclaw 启动脚本是 #!/usr/bin/env node，
# PATH 缺 node 会 127（2026-11-10 真跑实锤）⇒ 显式补齐 node 所在目录
_NODE_DIRS = [p for p in ('/opt/homebrew/bin', '/usr/local/bin')
              if (Path(p) / 'node').exists()]


def _subprocess_env():
    env = os.environ.copy()
    env['PATH'] = ':'.join(_NODE_DIRS + [env.get('PATH', '')]).rstrip(':')
    return env


def log(msg):
    print(f'[XiaotuoDaily {datetime.now():%H:%M:%S}] {msg}', flush=True)


def _json_call(method, url, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = _req.Request(url, data=data, method=method,
                       headers={'Content-Type': 'application/json'})
    with _req.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or '{}')


def load_duty():
    # 活库（WAL）只读一律普通 rw 句柄——mode=ro 可能建不了 -shm，immutable 会忽略 -wal（AGENTS.md §16 Y）
    conn = sqlite3.connect(str(DB), timeout=10)
    try:
        row = conn.execute('SELECT * FROM emp_duties WHERE agent_id = ?',
                           (AGENT_ID,)).fetchone()
        cols = [d[0] for d in conn.execute(
            'SELECT * FROM emp_duties LIMIT 0').description]
        return dict(zip(cols, row)) if row else None
    finally:
        conn.close()


def write_report(summary, detail):
    """通知管理员 + 回写名片近期动态。失败只记日志，不抛。"""
    now_ms = int(time.time() * 1000)
    try:
        conn = sqlite3.connect(str(DB), timeout=15)
        try:
            conn.execute(
                'INSERT INTO notifications (id, user_id, agent_id, type, title, '
                'content, read, created_at) VALUES (?,?,?,?,?,?,0,?)',
                ('notif_' + uuid.uuid4().hex[:12], ADMIN_ID, AGENT_ID,
                 'task_reminder',
                 f'小拓日报 · {datetime.now():%m月%d日}', detail, now_ms))
            conn.execute(
                'UPDATE emp_duties SET last_run_at = ?, last_run_summary = ?, '
                'updated_at = ? WHERE agent_id = ?',
                (now_ms, summary, now_ms, AGENT_ID))
            conn.commit()
        finally:
            conn.close()
        log('日报已写入 notifications + emp_duties.last_run_*')
    except Exception as e:
        log(f'写报告失败（不阻断）: {e}')


# ──────────────────────────── 小拓的脑子（验收必修②） ────────────────────────────
# 判定三关：排除 / 存疑 / 值得联系。新候选人未经判定不入「建议人工过目」。
# openclaw 输出提取照抄 solobrave-server.py::_extract_text_from_openclaw_output（32487 行，
# 主格式 result.payloads[].text 拼接）；本脚本独立运行不 import 服务端。

_VERDICTS = ('排除', '存疑', '值得联系')


def _extract_text_from_openclaw_output(obj):
    """从 OpenClaw JSON 输出中尽量提取文本回复（与服务端同款兼容逻辑）。"""
    if isinstance(obj, str):
        return obj if obj.strip() else None
    if isinstance(obj, list):
        texts = []
        for item in obj:
            if isinstance(item, dict) and isinstance(item.get('text'), str) and item['text'].strip():
                texts.append(item['text'])
            else:
                text = _extract_text_from_openclaw_output(item)
                if text:
                    texts.append(text)
        if texts:
            return '\n'.join(texts)
    if isinstance(obj, dict):
        if 'result' in obj and isinstance(obj['result'], dict):
            payloads = obj['result'].get('payloads')
            if isinstance(payloads, list):
                result_text = _extract_text_from_openclaw_output(payloads)
                if result_text:
                    return result_text
            for key in ('text', 'content', 'message'):
                val = obj['result'].get(key)
                if isinstance(val, str) and val.strip():
                    return val
        if 'outputs' in obj:
            return _extract_text_from_openclaw_output(obj['outputs'])
        for key in ('text', 'content', 'message', 'output', 'response', 'reply', 'answer'):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val
            if isinstance(val, (dict, list)):
                text = _extract_text_from_openclaw_output(val)
                if text:
                    return text
        if 'choices' in obj:
            return _extract_text_from_openclaw_output(obj['choices'])
        if 'payloads' in obj and isinstance(obj['payloads'], list):
            return _extract_text_from_openclaw_output(obj['payloads'])
        # ★ CLI 等到超时时返回运行快照而非 result 信封（2026-11-10 真跑实锤）：
        #   finalAssistantVisibleText / terminalReply.text 是快照里的成品回复
        fav = obj.get('finalAssistantVisibleText')
        if isinstance(fav, str) and fav.strip():
            return fav
        tr = obj.get('terminalReply')
        if isinstance(tr, dict):
            text = _extract_text_from_openclaw_output(tr)
            if text:
                return text
    return None


def build_judgment_prompt(item):
    """用 item 已有字段（真机采的，不造数）构造小拓判定 prompt。纯函数。"""
    settle = item.get('30天结算额')
    if isinstance(settle, list) and len(settle) == 2:
        settle_txt = f'{settle[0]:g}-{settle[1]:g} 元（平台只给区间）'
        low = settle[0]
    else:
        settle_txt = '未知'
        low = None
    cate = item.get('主推类目')
    cate_txt = '/'.join(cate) if isinstance(cate, list) else (cate or '未知')
    fans = item.get('粉丝指数')
    return (
        '你是商务助理小拓，正在审核今天从抖音达人广场新入库的候选带货达人，'
        '判断是否值得运营花时间去联系合作。只依据下面给出的信息判断，不要编造。\n\n'
        f"- 昵称：{item.get('昵称', '?')}\n"
        f"- 抖音号：{item.get('抖音号', '?')}\n"
        f"- 粉丝数：{fans if fans is not None else '未知'}\n"
        f"- 近30天结算额区间：{settle_txt}\n"
        f"- 主推类目：{cate_txt}\n"
        f"- 联系方式：{item.get('联系方式可得性', '未知')}\n\n"
        '判定标准（三选一）：\n'
        + (f'- 值得联系：结算额区间下沿 ≥ 1000 元；或下沿在 100~1000 元但联系方式可得（有）\n'
           if low is not None else '- 值得联系：带货数据看起来已被验证\n')
        + (f'- 排除：结算额区间下沿 < 100 元（近30天基本无成交，带货能力未验证）\n'
           if low is not None else '')
        + '- 存疑：其余情况，或关键信息不足\n\n'
        '只输出两行：\n'
        '第一行：排除 / 存疑 / 值得联系（三选一，只写这三个词之一）\n'
        '第二行：一句话理由（不超过 30 字）'
    )


def parse_judgment(text):
    """从 LLM 回复里按位置找第一个三态词，剩余作理由。任何异常 → 存疑兜底。纯函数。"""
    if not text or not text.strip():
        return ('存疑', '判定输出为空，转人工过目')
    text = text.strip()
    # 第一行若恰好是判定词，优先用（模型守规矩时的快路径）
    first_line = text.splitlines()[0].strip().strip('。*：:，,')
    if first_line in _VERDICTS:
        reason = '\n'.join(text.splitlines()[1:]).strip()
        return (first_line, (reason[:60] if reason else f'判定为{first_line}，无理由'))
    # 否则按位置找第一个三态词
    pos = [(text.find(v), v) for v in _VERDICTS if text.find(v) >= 0]
    if not pos:
        return ('存疑', '判定词缺失，转人工过目')
    _, verdict = min(pos)
    idx = text.find(verdict)
    reason = text[idx + len(verdict):].strip().strip('：:，,。*-— \n')
    return (verdict, (reason[:60] if reason else f'判定为{verdict}，无理由'))


def judge_item(item, timeout=JUDGE_TIMEOUT):
    """调小拓（openclaw CLI 走它自己的 SOUL.md / kimi_proxy_xiaotuo）做一次判定。

    任何失败（CLI 不在/超时/非 JSON/空回复）都落 ('存疑', '判定通道异常，转人工过目')，
    绝不抛出——判定是日报的增强项，不是阻断项。
    """
    prompt = build_judgment_prompt(item)
    args = [OPENCLAW_CLI, 'agent', '--agent', AGENT_ID, '--message', prompt,
            '--json', '--timeout', str(timeout)]
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                encoding='utf-8', errors='replace',
                                timeout=timeout + 60, env=_subprocess_env())
        if result.returncode != 0:
            log(f'判定通道退出码 {result.returncode}：{(result.stderr or "")[:200]}')
            return ('存疑', '判定通道异常，转人工过目')
        try:
            output = json.loads(result.stdout)
        except Exception:
            log(f'判定输出非 JSON：{(result.stdout or "")[:200]}')
            return ('存疑', '判定通道异常，转人工过目')
        content = _extract_text_from_openclaw_output(output)
        if not content:
            return ('存疑', '判定通道异常，转人工过目')
        return parse_judgment(content)
    except Exception as e:
        log(f'判定通道异常：{e}')
        return ('存疑', '判定通道异常，转人工过目')


def save_judgment(talent_id, verdict, reason):
    """判定结论回写 talents.remark JSON（合并 square_judgment 键，不动原有内容）。失败只记日志。"""
    try:
        conn = sqlite3.connect(str(DB), timeout=15)
        try:
            row = conn.execute('SELECT remark FROM talents WHERE id = ?',
                               (talent_id,)).fetchone()
            if not row:
                log(f'remark 回写跳过：达人 {talent_id} 不存在')
                return
            try:
                rem = json.loads(row[0]) if row[0] else {}
            except Exception:
                log(f'remark 回写跳过：达人 {talent_id} 的 remark 非 JSON，不覆盖')
                return
            if not isinstance(rem, dict):
                log(f'remark 回写跳过：达人 {talent_id} 的 remark 不是对象')
                return
            rem['square_judgment'] = {
                'verdict': verdict,
                'reason': reason,
                'judged_at': int(time.time() * 1000),
                'judged_by': AGENT_ID,
            }
            conn.execute('UPDATE talents SET remark = ? WHERE id = ?',
                         (json.dumps(rem, ensure_ascii=False), talent_id))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log(f'remark 回写失败（不阻断）：{e}')


_INSERTED_RE = re.compile(r'inserted（(.+?)）')


def _inserted_talent_id(item):
    m = _INSERTED_RE.search(str(item.get('入库', '')))
    return m.group(1) if m else None


def main():
    duty = load_duty()
    if not duty:
        log('emp_duties 没有小拓职责行，退出（先 PUT /api/agents/emp_xiaotuo/duties）')
        return 0
    category = (duty.get('category') or '').strip()
    quota = duty.get('quota') or 5
    log(f'职责行：category={category or "全部"} quota={quota}')

    # ① 触发扫描
    try:
        resp = _json_call('POST', f'{WORKER}/square-scan',
                          {'category': category, 'quota': quota})
    except Exception as e:
        detail = f'今日深看0 | 新增0 | 排除0（worker 不可达：{e}）| 建议人工过目：检查 buyin_worker 是否在跑'
        write_report('worker 不可达，今日未跑', detail)
        log(detail)
        return 0
    if not resp.get('ok'):
        err = resp.get('error') or resp.get('status') or 'unknown'
        detail = f'今日深看0 | 新增0 | 排除0（{err}）| 建议人工过目：无需'
        write_report(f'未执行：{err}', detail)
        log(detail)
        return 0
    cid = resp['collect_id']
    log(f'任务已入队 {cid}，开始轮询')

    # ② 轮询到终态
    deadline = time.time() + TIMEOUT_SEC
    job = {}
    while time.time() < deadline:
        time.sleep(POLL_SEC)
        try:
            job = _json_call('GET', f'{WORKER}/jobs/{cid}', timeout=15)
        except Exception as e:
            log(f'轮询网络错误（继续）: {e}')
            continue
        status = job.get('status')
        if status in ('done', 'failed', 'blocked_risk', 'need_login', 'refused'):
            log(f'终态 {status}（stage={job.get("stage", "")}）')
            break
    else:
        job = {'status': 'timeout', 'error': f'{TIMEOUT_SEC // 60} 分钟未出终态'}

    # ③ 日报（简报格式：今日深看N | 新增N | 排除N（主因）| 建议人工过目：XXX）
    status = job.get('status')
    if status == 'done':
        result = json.loads(job.get('result') or '{}')
        scanned = result.get('scanned', 0)
        inserted = result.get('inserted', 0)
        dups = result.get('duplicates', 0)
        skipped = result.get('skipped', 0)
        reasons = []
        if dups:
            reasons.append(f'重复 {dups}')
        if skipped:
            reasons.append(f'缺抖音号 {skipped}')
        main_reason = ' / '.join(reasons) if reasons else '无排除'
        excluded = dups + skipped

        # 必修②：每个新入库候选人过小拓自己的判定（三关），结论回写 remark + 进日报
        verdict_counts = {v: 0 for v in _VERDICTS}
        eyeball = []
        for it in result.get('items') or []:
            tid = _inserted_talent_id(it)
            if not tid:
                continue
            verdict, reason = judge_item(it)
            verdict_counts[verdict] += 1
            save_judgment(tid, verdict, reason)
            log(f'判定 {it.get("昵称", "?")}（{tid}）：{verdict} — {reason}')
            if verdict in ('值得联系', '存疑'):   # 这两态才是要人过目的货
                eyeball.append(f'{it.get("昵称", "?")}（{verdict}：{reason}）')
        judge_txt = (f'判定：值得联系{verdict_counts["值得联系"]} / '
                     f'存疑{verdict_counts["存疑"]} / 排除{verdict_counts["排除"]}')
        eyeball_txt = '；'.join(eyeball) if eyeball else '无'
        detail = (f'今日深看{scanned} | 新增{inserted}（{judge_txt}）'
                  f' | 排除{excluded}（{main_reason}）'
                  f' | 建议人工过目：{eyeball_txt}')
        summary = (f'今日筛出 {scanned} 位候选，新入库 {inserted} 位'
                   f'（值得联系 {verdict_counts["值得联系"]} / 存疑 {verdict_counts["存疑"]} / '
                   f'排除 {verdict_counts["排除"]}）')
    else:
        err = job.get('error') or status
        detail = f'今日深看0 | 新增0 | 排除0（{status}：{err}）| 建议人工过目：' + (
            '风控停手，等环境冷却人工检查' if status == 'blocked_risk'
            else '团长端登录态失效，需要扫码重登' if status == 'need_login'
            else '查看 worker 日志定位原因')
        summary = f'今日未跑成（{status}）'

    write_report(summary, detail)
    log(detail)
    return 0


if __name__ == '__main__':
    sys.exit(main())
