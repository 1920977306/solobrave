#!/usr/bin/env python3
"""
主管三件套 (supervisor + coder + reviewer) 一键部署 —— SoloBrave 任务2

基于 OpenClaw subagent 调用链:
  用户 → 主管(sb_supervisor) → sessions_spawn → 码农(sb_coder)
       → 主管收 announce → sessions_spawn → 评审(sb_reviewer)
       → 主管收 announce → 合成最终答复 (FAIL 则带问题清单再打一轮 coder, 最多 2 轮)

幂等: 可重复执行; patch 前自动备份 openclaw.json。

用法:
  python3 setup_supervisor_trio.py            # 部署
  python3 setup_supervisor_trio.py --verify   # 只校验现有配置
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
BASE = Path.home() / ".openclaw"
AGENTS = {
    "sb_supervisor": {
        "dir": BASE / "agents" / "sb_supervisor",
        "workspace": BASE / "workspace-sb" / "supervisor",
        "name": "主管",
        "model": {"primary": "kimi/k3", "fallbacks": ["zhipu/glm-4-flash", "minimax/MiniMax-M3"]},
        "extra": {
            "subagents": {
                "allowAgents": ["sb_coder", "sb_reviewer"],
                "requireAgentId": True,
                "delegationMode": "prefer",
                # 注意: maxChildrenPerAgent 是 agents.defaults.subagents 全局键,
                # per-agent 不收 (schema 校验), 用全局默认 5
            }
        },
        "soul": """# 主管 (Supervisor) —— SoloBrave 编排 agent

你是主管, 负责把用户的开发/分析任务拆给两个下属 subagent 完成, 自己不直接干活。

## 固定调用链 (必须遵守)
1. 收到任务后, 先用 1-3 句话向用户确认理解(可选), 然后:
   - `sessions_spawn` 发给 coder (agentId: "sb_coder", taskName: "coder_run", context: "isolated"),
     task 里写清: 目标、文件路径、验收标准、输出格式(纯文本报告, 不要废话)。
2. spawn 后立刻 `sessions_yield` 等待, 不要轮询。
3. 收到 coder 的完成 announce 后:
   - `sessions_spawn` 发给 reviewer (agentId: "sb_reviewer", taskName: "review_run", context: "isolated"),
     task 里附: 原始需求 + coder 的完整产出, 要求 reviewer 逐条核对验收标准。
4. 收到 reviewer 的 announce 后:
   - PASS → 把 reviewer 结论 + coder 产出要点合成最终答复给用户(注明文件路径)。
   - FAIL → 把 reviewer 的问题清单作为新 task 再 spawn 一轮 coder (taskName: "coder_fix"), 修完再过 reviewer, 最多 2 轮; 仍不过则如实向用户汇报卡点。

## 红线
- 禁止自己写代码/自己跑工具干活, 只能拆分、转述、合成。
- spawn 必须显式带 agentId (只允许 sb_coder / sb_reviewer)。
- 用中文答复用户。最终答复格式: 结论(✅/❌) → 产出位置 → reviewer 核对摘要。
""",
    },
    "sb_coder": {
        "dir": BASE / "agents" / "sb_coder",
        "workspace": BASE / "workspace-sb" / "coder",
        "name": "码农",
        "model": {"primary": "kimi/k3", "fallbacks": ["zhipu/glm-4-flash", "minimax/MiniMax-M3"]},
        "extra": {},
        "soul": """# 码农 (Coder) —— SoloBrave 实现 agent

你只做实现, 不做评审, 不直接面向用户。

## 规则
- 收到 [Subagent Task] 后, 在指定 workspace 内完成任务(读写文件、跑命令、写测试)。
- 完成后用纯文本报告回复(这是给主管 announce 的内容), 格式:
  ## 完成报告
  - 改动文件: <绝对路径列表>
  - 实现要点: <3-5 条>
  - 自测结果: <跑了什么命令, 结果如何>
  - 遗留问题: <没有则写"无">
- 不写 session/message 工具调用; 不做与 task 无关的事。
""",
    },
    "sb_reviewer": {
        "dir": BASE / "agents" / "sb_reviewer",
        "workspace": BASE / "workspace-sb" / "reviewer",
        "name": "评审",
        "model": {"primary": "kimi/k3", "fallbacks": ["zhipu/glm-4-flash", "minimax/MiniMax-M3"]},
        "extra": {},
        "soul": """# 评审 (Reviewer) —— SoloBrave 质检 agent

你只评审 coder 的产出, 不做修改, 不直接面向用户。

## 规则
- 收到 [Subagent Task] (含原始需求 + coder 产出) 后, 逐条核对验收标准:
  1. 需求覆盖: 每条验收标准是否满足;
  2. 正确性: 实际读文件/跑测试验证, 不信 coder 的一面之词;
  3. 破坏性: 是否改了不该改的文件。
- 跑代码/测试前必须先 cd 到目标文件所在目录(或用绝对路径 + 正确的 import 方式);
  上次评审曾因在错误目录运行而误报 NameError, 主管已复核推翻。
- 用纯文本报告回复(给主管 announce), 格式:
  ## 评审报告
  - 结论: PASS 或 FAIL
  - 逐条核对: <验收标准 → 满足/不满足 + 证据>
  - 问题清单: <FAIL 时必填, 每条给修复建议; PASS 写"无">
""",
    },
}


def _oc_get(key: str) -> str:
    proc = subprocess.run(["openclaw", "config", "get", key], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def deploy() -> None:
    entries = {}
    for aid, spec in AGENTS.items():
        spec["dir"].mkdir(parents=True, exist_ok=True)
        spec["workspace"].mkdir(parents=True, exist_ok=True)
        (spec["dir"] / "SOUL.md").write_text(spec["soul"], encoding="utf-8")
        entry = {
            "name": spec["name"],
            "identity": {"name": spec["name"]},
            "workspace": str(spec["workspace"]),
            "agentDir": str(spec["dir"]),
            "model": spec["model"],
        }
        entry.update(spec["extra"])
        entries[aid] = entry
        print(f"  {aid}: SOUL.md + workspace 就绪")

    bak = OPENCLAW_CONFIG.parent / f"openclaw.json.bak.trio.{datetime.now():%Y%m%d_%H%M%S}"
    bak.write_bytes(OPENCLAW_CONFIG.read_bytes())
    print(f"Backup → {bak.name}")

    patch = {"agents": {"entries": entries}}
    proc = subprocess.run(
        ["openclaw", "config", "patch", "--stdin"],
        input=json.dumps(patch), text=True, capture_output=True,
    )
    if proc.returncode != 0:
        print("PATCH FAILED:", proc.stderr.strip())
        sys.exit(1)
    print(proc.stdout.strip())
    verify()


def verify() -> None:
    print("\n=== verify ===")
    for aid in AGENTS:
        model = _oc_get(f"agents.entries.{aid}.model.primary")
        print(f"  {aid}: model={model or '(继承默认)'}")
    allow = _oc_get("agents.entries.sb_supervisor.subagents.allowAgents")
    print(f"  supervisor allowAgents = {allow}")
    proc = subprocess.run(["openclaw", "agents", "list"], capture_output=True, text=True)
    hit = [l for l in proc.stdout.splitlines() if "sb_" in l]
    print(f"  openclaw agents list 含三件套: {len(hit)} 行 (退出码 {proc.returncode})")
    for l in hit:
        print("   ", l.strip())


if __name__ == "__main__":
    if "--verify" in sys.argv:
        verify()
    else:
        deploy()
