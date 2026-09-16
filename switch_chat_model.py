#!/usr/bin/env python3
"""
一键切换 OpenClaw Gateway 的 chat 模型（前端 chat 走 OpenClaw WS，所以切这里）。

支持的 profile:
  zhipu   → 智谱 GLM-4-flash（快 + 便宜，测试首选）
  kimi    → Kimi K3（强，生产）
  restore → 恢复到切换前的默认（kimi_proxy_* per-agent）

用法:
  python switch_chat_model.py zhipu
  python switch_chat_model.py kimi
  python switch_chat_model.py restore

设计原则（最小入侵 + 留回滚）:
  - 每次切换前自动备份当前配置到 ~/.openclaw/openclaw.json.bak.switch.<ts>
  - 用 `openclaw config patch` 走 schema 校验，不会写出非法配置
  - patch 后 `openclaw gateway stop` 让 supervisor 自动重启（≈5s 断流）
  - 切换后用 `openclaw models list --agent <id>` 验证 default model

为什么需要这个 script:
  前端 chat 走 OpenClaw Gateway (18789) 直连，不经过 solobrave 后端。
  solobrave 侧的 AI override 代码是防御层，没法独立切前端 chat。
  真正影响前端 chat 的就是 OpenClaw 配置本身，所以切换点在 OpenClaw。
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
BACKUP_PREFIX = "openclaw.json.bak.switch"

# 6 个员工的 agent id（与 openclaw.json 当前 entries 一致；如果新员工手动加）
AGENT_IDS = [
    "emp_1779430403964",   # 貂蝉
    "emp_1779955656118",   # 上官婉儿
    "emp_1780132768182",   # 孔明
    "emp_1780199176680",   # Helen
    "emp_1787202695740_4386",
    "emp_1787550091124_1048",
]

# 从 solobrave .env 读智谱 key（不入 chat 历史）
def _read_zhipu_key() -> str:
    env = Path("/Users/qichen/solobrave-prod/.env")
    if not env.is_file():
        sys.exit("ERR: /Users/qichen/solobrave-prod/.env not found")
    for line in env.read_text().splitlines():
        if line.startswith("SOLOBRAVE_AI_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("ERR: SOLOBRAVE_AI_API_KEY not in .env")


def _backup() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = OPENCLAW_CONFIG.parent / f"{BACKUP_PREFIX}.{ts}"
    bak.write_bytes(OPENCLAW_CONFIG.read_bytes())
    return bak


def _patch(patch_obj: dict, dry: bool = False) -> None:
    cmd = ["openclaw", "config", "patch", "--stdin"]
    if dry:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, input=json.dumps(patch_obj), text=True,
                          capture_output=True)
    if proc.returncode != 0:
        print("PATCH FAILED:", proc.stderr.strip())
        sys.exit(1)
    print(proc.stdout.strip())


# OpenClaw 实际支持 hot reload（patch 输出 "Change will apply without restarting"），
# 不再强制 stop gateway。保留 reload 触发逻辑（如未来 schema 改了要重启）。
def _restart_gateway() -> None:
    pass


def _verify(profile: str) -> None:
    # profile=zhipu: 期望模型名含 glm-4-flash
    # profile=kimi: 期望模型名含 k3
    want = "glm-4-flash" if profile == "zhipu" else "k3"
    print(f"  (expecting '{want}' in model name)")
    for agent_id in AGENT_IDS:
        proc = subprocess.run(
            ["openclaw", "config", "get", f"agents.entries.{agent_id}.model.primary"],
            capture_output=True, text=True,
        )
        actual = proc.stdout.strip()
        ok = "✅" if want in actual else "❌"
        print(f"  {ok} {agent_id}: {actual}")


def profile_zhipu() -> None:
    key = _read_zhipu_key()
    patch = {
        "env": {"vars": {"OPENCLAW_ZHIPU_API_KEY": key}},
        "models": {
            "providers": {
                "zhipu": {
                    "baseUrl": "https://open.bigmodel.cn/api/paas/v4/",
                    "api": "openai-completions",
                    "apiKey": key,
                    "models": [
                        {
                            "id": "glm-4-flash",
                            "name": "GLM-4 Flash",
                            "reasoning": False,
                            "input": ["text"],
                            "contextWindow": 128000,
                            "maxTokens": 8192,
                        }
                    ],
                }
            }
        },
        "auth": {"profiles": {"zhipu:default": {"provider": "zhipu", "mode": "api_key"}}},
        "agents": {"defaults": {"model": {"primary": "zhipu/glm-4-flash"}}},
    }
    for aid in AGENT_IDS:
        patch["agents"].setdefault("entries", {})[aid] = {"model": {"primary": "zhipu/glm-4-flash"}}

    bak = _backup()
    print(f"Backup → {bak.name}")
    _patch(patch)
    _restart_gateway()
    print("Verify (zhipu):")
    _verify("zhipu")


def profile_kimi() -> None:
    # Kimi K3 走 4 个员工各自的 kimi_proxy_<name> provider（按员工隔离 API key）
    # 已经是 openclaw.json 当前默认（pre-zhipu.bak 状态）
    patch = {
        "agents": {
            "defaults": {"model": {"primary": "kimi/k3"}},
            "entries": {
                "emp_1779430403964": {"model": {"primary": "kimi_proxy_diaochan/k3"}},
                "emp_1779955656118": {"model": {"primary": "kimi_proxy_shangguan/k3"}},
                "emp_1780132768182": {"model": {"primary": "kimi_proxy_kongming/k3"}},
                "emp_1780199176680": {"model": {"primary": "kimi_proxy_helen/k3"}},
                "emp_1787202695740_4386": {"model": {"primary": "kimi/k3"}},
                "emp_1787550091124_1048": {"model": {"primary": "kimi/k3"}},
            },
        }
    }
    bak = _backup()
    print(f"Backup → {bak.name}")
    _patch(patch)
    _restart_gateway()
    print("Verify (kimi):")
    _verify("kimi")


def profile_restore() -> None:
    # 找最近的 switch 备份前的配置（pre-zhipu 之前的最后一份 = zhipu 切换前）
    # 优先用 pre-zhipu 备份（= 原始 kimi 配置），如果没有再找最近的 switch backup
    pre_zhipu = sorted(OPENCLAW_CONFIG.parent.glob("openclaw.json.bak.pre-zhipu.*"))
    if pre_zhipu:
        src = pre_zhipu[-1]
    else:
        switch_baks = sorted(OPENCLAW_CONFIG.parent.glob(f"{BACKUP_PREFIX}.*"))
        if not switch_baks:
            sys.exit("ERR: no backup found")
        src = switch_baks[-1]
    print(f"Restoring from {src.name}")
    OPENCLAW_CONFIG.write_bytes(src.read_bytes())
    _restart_gateway()


PROFILES = {
    "zhipu": profile_zhipu,
    "kimi": profile_kimi,
    "restore": profile_restore,
}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in PROFILES:
        print(__doc__)
        print(f"\nUnknown profile. Choices: {', '.join(PROFILES)}")
        sys.exit(2)
    PROFILES[sys.argv[1]]()


if __name__ == "__main__":
    main()