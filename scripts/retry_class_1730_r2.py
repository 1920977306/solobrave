#!/usr/bin/env python3
"""1730 班人工重试驱动（2026-11-18 19:5x，A 边）。

背景：19:19 首投 turn 49641dd7 因模型过度调研（读 worker 源码/翻历史通知）
耗尽长度预算 → non_deliverable_terminal_turn（stopReason=length），未交卷；
CLI 早已返回 rc=0（投递语义盲区：终态错误 turn 也返回 0），xiaotuo_daily
在主看门循环空等。本驱动复用 xiaotuo_daily 的原装函数做一次 -r2 重投，
消息加「禁止调研」硬约束。若 20:18 前交卷，旧看门循环会自动认领收工；
若没赶上，旧看门按设计告警——两种结局都有机制背书。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, '/Users/qichen/solobrave-prod')
import xiaotuo_daily as xd

HARD_LINE = (
    '\n★ 本班为人工触发重试班（首 turn 因过度调研耗尽长度预算未交卷）。'
    '铁律：广场扫描工具的输出即全部依据——禁止再读 worker 源码、'
    '禁止翻历史通知/日志做调研，拿到 scan 结果直接写日报交卷。'
    '存疑判据从简，不再二次核实台账。'
)


def main():
    duty = xd.load_duty()
    if not duty:
        print('无职责行，退出')
        return 1
    slot_start, slot_label = xd.class_slot()
    print(f'当前班次槽：{slot_label}')
    key = xd.daily_session_key(slot_label) + '-r2'
    class_no = xd.class_ordinal(slot_label)
    msg = xd.build_task_message(duty, slot_label=slot_label, class_no=class_no,
                                class_total=len(xd.CLASS_SLOTS)) + HARD_LINE
    print(f'重投（key={key}）：类目={duty.get("category") or "见任务书"}')
    note, run_id, rc = xd.deliver_task(msg, session_key=key)
    print(f'重投结果：{note}' + (f'（runId={run_id}）' if run_id else ''))
    # 看到交卷为止（上限 35 分钟；旧看门 20:18 也会兜底告警）
    deadline = time.time() + 35 * 60
    while time.time() < deadline:
        if xd.report_delivered(int(time.time() * 1000) - 40 * 60 * 1000):
            print('重试班已交日报，收工')
            return 0
        time.sleep(60)
    print('35 分钟未见交卷——旧看门/验卷链将按设计接手')
    return 0


if __name__ == '__main__':
    sys.exit(main())
