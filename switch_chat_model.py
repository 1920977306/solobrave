#!/usr/bin/env python3
"""
【已废弃硬切】OpenClaw Gateway chat 模型只读诊断 + 推荐配置生成器

2026-09-30 起, 全局硬切逻辑废弃:
  - 前端 pill 改为「请求级模型指定」(REQUEST_CHAT_MODEL → sessions.patch → chat.send
    → lifecycle 结束自动还原), 不改全局配置, 不影响其他客户
  - 本脚本默认只读: 打印当前配置 + 生成推荐 patch, 不落盘
  - 确需改全局默认(如全员降级应急)时, 显式加 --apply, 且每次仍自动备份

架构原则（硬约束）: 前端 chat 必须经 OpenClaw Gateway，不允许任何路径
直连外部 LLM provider API（前端/solobrave BFF 都不行）。所有模型切换都
在 OpenClaw 配置层做。

支持的 profile:
  zhipu   → 智谱 GLM-4-flash（快 + 便宜，测试首选）
  kimi    → Kimi K3（强，按员工隔离 4 个 kimi_proxy_<name>）
  minimax → MiniMax M2/M3（强 + 通用，配置走 env vars）
  restore → 恢复到切换前的默认（pre-zhipu.bak 备份）

用法:
  python switch_chat_model.py zhipu              # 只读: 当前配置 + 推荐 patch
  python switch_chat_model.py zhipu --apply      # 废弃例外: 真正打 patch (全局硬切)
  python switch_chat_model.py restore --apply    # 从备份恢复 (需 --apply)

minimax 配置（设到 solobrave .env）:
  OPENCLAW_MINIMAX_API_KEY=<your-minimax-key>
  OPENCLAW_MINIMAX_BASE_URL=https://api.minimaxi.com/anthropic  (国内, 默认)
                       或 https://api.minimax.io/anthropic        (海外)
  OPENCLAW_MINIMAX_MODEL=MiniMax-M3                              (默认; M2 / M2-mini 也行)

注: Kimi (coding/) 和 MiniMax (anthropic/) 都是 coding 模型，OpenClaw 协议
    都用 anthropic-messages; zhipu 是通用 chat，用 openai-completions。
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

APPLY = "--apply" in sys.argv
PROFILE_ARGS = [a for a in sys.argv[1:] if not a.startswith("-")]

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


def _verify(want_substr: str) -> None:
    print(f"  (expecting '{want_substr}' in model name)")
    for agent_id in AGENT_IDS:
        proc = subprocess.run(
            ["openclaw", "config", "get", f"agents.entries.{agent_id}.model.primary"],
            capture_output=True, text=True,
        )
        actual = proc.stdout.strip()
        ok = "✅" if want_substr in actual else "❌"
        print(f"  {ok} {agent_id}: {actual}")


def _read_current() -> str:
    proc = subprocess.run(
        ["openclaw", "config", "get", "agents.defaults.model.primary"],
        capture_output=True, text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else f"<read failed: {proc.stderr.strip()[:120]}>"


def _mask_secrets(obj):
    """打印推荐配置前脱敏: apiKey/token 类字段替换为 *** (防止密钥进终端/日志)"""
    if isinstance(obj, dict):
        return {k: ('***' if any(t in k.lower() for t in ('key', 'token', 'secret')) else _mask_secrets(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_secrets(v) for v in obj]
    return obj


def _print_readonly_header(profile: str) -> None:
    print("=" * 64)
    print(f"[只读模式] profile={profile}  (加 --apply 才真正修改全局配置)")
    print(f"当前 agents.defaults.model.primary = {_read_current()}")
    print("推荐配置 patch JSON 如下, 人工核对后可:")
    print(f"  1) python switch_chat_model.py {profile} --apply")
    print("  2) 或手动: openclaw config patch --stdin < patch.json")
    print("=" * 64)


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

    if not APPLY:
        _print_readonly_header("zhipu")
        print(json.dumps(_mask_secrets(patch), ensure_ascii=False, indent=2))
        return
    bak = _backup()
    print(f"Backup → {bak.name}")
    _patch(patch)
    print("Verify (zhipu):")
    _verify("glm-4-flash")


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
    if not APPLY:
        _print_readonly_header("kimi")
        print(json.dumps(_mask_secrets(patch), ensure_ascii=False, indent=2))
        return
    bak = _backup()
    print(f"Backup → {bak.name}")
    _patch(patch)
    print("Verify (kimi):")
    _verify("k3")


def profile_minimax() -> None:
    """Kimi 和 MiniMax 都是 coding 模型，走 Anthropic-messages 协议（与 Kimi 同结构）。

    baseUrl 默认国内 MiniMax coding endpoint；海外用 https://api.minimax.io/anthropic。
    model 默认 MiniMax-M3；可改 M2 / M2-mini。
    """
    key = _read_env("OPENCLAW_MINIMAX_API_KEY")
    # ★ MiniMax Anthropic 兼容 endpoint 是 /anthropic（不是 /coding/，那是 Kimi 的）
    base_url = _read_env("OPENCLAW_MINIMAX_BASE_URL", required=False) or "https://api.minimaxi.com/anthropic"
    model = _read_env("OPENCLAW_MINIMAX_MODEL", required=False) or "MiniMax-M3"
    provider_id = "minimax"
    model_ref = f"{provider_id}/{model}"

    patch = {
        "env": {"vars": {f"OPENCLAW_{provider_id.upper()}_API_KEY": key}},
        "models": {
            "providers": {
                provider_id: {
                    "baseUrl": base_url,
                    "api": "anthropic-messages",  # ★ Kimi/MiniMax 都是 coding 协议
                    "apiKey": key,
                    "models": [
                        {
                            "id": model,
                            "name": f"MiniMax {model}",
                            "reasoning": True,
                            "input": ["text"],
                            "contextWindow": 200000,
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

    if not APPLY:
        _print_readonly_header(f"minimax={model} @ {base_url}")
        print(json.dumps(_mask_secrets(patch), ensure_ascii=False, indent=2))
        return
    bak = _backup()
    print(f"Backup → {bak.name}")
    _patch(patch)
    print(f"Verify (minimax={model} @ {base_url}):")
    _verify("minimax")


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
    if not APPLY:
        _print_readonly_header("restore")
        print(f"将用备份恢复: {src.name}")
        print("确认后执行: python switch_chat_model.py restore --apply")
        return
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
    if len(PROFILE_ARGS) != 1 or PROFILE_ARGS[0] not in PROFILES:
        print(__doc__)
        print(f"\nUnknown profile. Choices: {', '.join(PROFILES)}")
        sys.exit(2)
    PROFILES[PROFILE_ARGS[0]]()


if __name__ == "__main__":
    main()
