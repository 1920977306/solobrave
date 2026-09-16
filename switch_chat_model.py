#!/usr/bin/env python3
"""
一键切换 OpenClaw Gateway 的 chat 模型（前端 chat 走 OpenClaw WS，所以切这里）。

架构原则（硬约束）: 前端 chat 必须经 OpenClaw Gateway，不允许任何路径
直连外部 LLM provider API（前端/solobrave BFF 都不行）。所有模型切换都
在 OpenClaw 配置层做。

支持的 profile:
  zhipu   → 智谱 GLM-4-flash（快 + 便宜，测试首选）
  kimi    → Kimi K3（强，按员工隔离 4 个 kimi_proxy_<name>）
  minimax → MiniMax M2/M3（强 + 通用，配置走 env vars）
  restore → 恢复到切换前的默认（pre-zhipu.bak 备份）

用法:
  python switch_chat_model.py zhipu
  python switch_chat_model.py kimi
  python switch_chat_model.py minimax
  python switch_chat_model.py restore

minimax 配置（设到 solobrave .env）:
  OPENCLAW_MINIMAX_API_KEY=<your-minimax-key>
  OPENCLAW_MINIMAX_BASE_URL=https://api.minimax.chat/v1  (海外 MiniMax, 默认)
                       或 https://api.MiniMax.cn/v1          (国内 MiniMax)
  OPENCLAW_MINIMAX_MODEL=MiniMax-M3                          (默认)

设计原则（最小入侵 + 留回滚）:
  - 每次切换前自动备份当前配置到 ~/.openclaw/openclaw.json.bak.switch.<ts>
  - 用 `openclaw config patch` 走 schema 校验，不会写出非法配置
  - OpenClaw 支持 hot reload，patch 后无需重启 gateway
  - 切换后用 `openclaw config get` 抽查员工 model.primary 验证生效
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
BACKUP_PREFIX = "openclaw.json.bak.switch"
SOLOBRAVE_ENV = Path("/Users/qichen/solobrave-prod/.env")

# 6 个员工的 agent id（与 openclaw.json 当前 entries 一致；如果新员工手动加）
AGENT_IDS = [
    "emp_1779430403964",   # 貂蝉
    "emp_1779955656118",   # 上官婉儿
    "emp_1780132768182",   # 孔明
    "emp_1780199176680",   # Helen
    "emp_1787202695740_4386",
    "emp_1787550091124_1048",
]


def _read_env(key: str, required: bool = True) -> str:
    """优先从 solobrave .env 读（不入 chat 历史），其次从 process env"""
    if SOLOBRAVE_ENV.is_file():
        for line in SOLOBRAVE_ENV.read_text().splitlines():
            if line.startswith(f"{key}="):
                val = line.split("=", 1)[1].strip()
                if val:
                    return val
    val = os.environ.get(key, "").strip()
    if not val and required:
        sys.exit(f"ERR: {key} not set in {SOLOBRAVE_ENV} or process env")
    return val


def _backup() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = OPENCLAW_CONFIG.parent / f"{BACKUP_PREFIX}.{ts}"
    bak.write_bytes(OPENCLAW_CONFIG.read_bytes())
    return bak


def _patch(patch_obj: dict) -> None:
    proc = subprocess.run(
        ["openclaw", "config", "patch", "--stdin"],
        input=json.dumps(patch_obj), text=True, capture_output=True,
    )
    if proc.returncode != 0:
        print("PATCH FAILED:", proc.stderr.strip())
        sys.exit(1)
    print(proc.stdout.strip())


# OpenClaw 支持 hot reload（patch 输出 "Change will apply without restarting"）
def _restart_gateway() -> None:
    pass


def _verify(profile: str, want_substr: str) -> None:
    print(f"  (expecting '{want_substr}' in model name)")
    for agent_id in AGENT_IDS:
        proc = subprocess.run(
            ["openclaw", "config", "get", f"agents.entries.{agent_id}.model.primary"],
            capture_output=True, text=True,
        )
        actual = proc.stdout.strip()
        ok = "✅" if want_substr in actual else "❌"
        print(f"  {ok} {agent_id}: {actual}")


def profile_zhipu() -> None:
    key = _read_env("SOLOBRAVE_AI_API_KEY")
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
    print("Verify (zhipu):")
    _verify("zhipu", "glm-4-flash")


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
    print("Verify (kimi):")
    _verify("kimi", "k3")


def profile_minimax() -> None:
    key = _read_env("OPENCLAW_MINIMAX_API_KEY")
    base_url = _read_env("OPENCLAW_MINIMAX_BASE_URL", required=False) or "https://api.minimax.chat/v1"
    model = _read_env("OPENCLAW_MINIMAX_MODEL", required=False) or "MiniMax-M3"
    provider_id = "minimax"
    model_ref = f"{provider_id}/{model}"

    patch = {
        "env": {"vars": {f"OPENCLAW_{provider_id.upper()}_API_KEY": key}},
        "models": {
            "providers": {
                provider_id: {
                    "baseUrl": base_url,
                    "api": "openai-completions",
                    "apiKey": key,
                    "models": [
                        {
                            "id": model,
                            "name": f"MiniMax {model}",
                            "reasoning": True,
                            "input": ["text"],
                            "contextWindow": 128000,
                            "maxTokens": 8192,
                        }
                    ],
                }
            }
        },
        "auth": {
            "profiles": {f"{provider_id}:default": {"provider": provider_id, "mode": "api_key"}},
        },
        "agents": {
            "defaults": {"model": {"primary": model_ref}},
            "entries": {aid: {"model": {"primary": model_ref}} for aid in AGENT_IDS},
        },
    }

    bak = _backup()
    print(f"Backup → {bak.name}")
    _patch(patch)
    print(f"Verify (minimax={model} @ {base_url}):")
    _verify("minimax", model)


def profile_restore() -> None:
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
    "minimax": profile_minimax,
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