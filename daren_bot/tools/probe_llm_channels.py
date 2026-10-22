#!/usr/bin/env python3
"""LLM 通道体检 —— 逐环真调一次，看现在**到底哪个能用**。

背景（2026-10-22 老大）：「kimi 现在没额度，只能你来」。
项目的问答/选品/识图都走**降级环**（套餐 API → Code 订阅 OAuth → kimi → minimax 开放平台），
所以「Kimi 没额度」本身**不致命** —— 只要环 0/环 1 通，用户就无感。
但「哪个环在顶」必须**实测**才知道，光看代码里的注释（写着 kimi 429 / minimax 402）
是 2026-10-21 的快照，今天可能又变了。

本脚本**逐环独立发一次真实请求**（不走降级逻辑，所以能看清每一环单独的死活），
输出每环的 HTTP 结果 / 耗时 / 错误摘要，并给出「当前能顶上的是哪一环」。

用法：
    python3 daren_bot/tools/probe_llm_channels.py            # 探文字链
    python3 daren_bot/tools/probe_llm_channels.py --vision   # 探识图链（更贵）

退出码：0 = 至少一环可用；1 = 全灭。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daren_bot import executors  # noqa: E402

PROMPT = "只回复两个字：可用"


def _probe_text_ring(ring: dict, idx: int) -> tuple:
    """真调一次文字环。返回 (状态, 说明)。状态 ∈ alive / empty / dead。"""
    t0 = time.time()
    try:
        out = executors._call_anthropic(ring, PROMPT) if ring.get("protocol") == "anthropic" \
            else executors._call_openai(ring, PROMPT)
        dt = (time.time() - t0) * 1000
        if out.strip():
            return 'alive', f"{dt:.0f}ms · 回复「{out.strip()[:20]}」"
        return 'empty', f"{dt:.0f}ms · 通道通但回复为空"
    except Exception as e:  # noqa: BLE001
        dt = (time.time() - t0) * 1000
        return 'dead', f"{dt:.0f}ms · {str(e)[:150]}"


def _probe_vision_ring(ring: dict, idx: int) -> tuple:
    """真调一次识图环。用一张 1x1 PNG 探「能不能发图」，不校验业务字段。

    ⚠️ 2026-10-22 修**假信号**（老大口径：「探针结论必先对真值」）：

    旧判据 = 「抽出的字段非空才算通」→ 1×1 像素图里**没有商品**，
    模型**诚实返回空字段**是**正确行为**（「不编造」原则），
    却被判成「通道死亡」→ 报出「识图链全灭」的**假警报**，
    差点让人去查一个根本没坏的通道。

    新判据 = **能不能发图并拿到响应**：
      HTTP 200 + body 里无错误 → 通道**活着**（哪怕字段为空）
      HTTP 错误 / body 里塞错误 → 通道**死了**

    两种结果分别标记 `alive` / `empty` / `dead`，让报告能区分
    「通道死了」与「通道活着但这张图里没东西可抽」。
    """
    import base64
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    b64 = base64.b64encode(png).decode()
    t0 = time.time()
    try:
        # 直接打原始端点，不走业务字段解析 —— 只判「请求是否成功」
        import urllib.request
        req = urllib.request.Request(
            ring['base'].rstrip('/') + '/chat/completions',
            data=json.dumps({
                'model': ring['model'],
                'messages': [{'role': 'user', 'content': [
                    {'type': 'text', 'text': '用中文一句话描述这张图。'},
                    {'type': 'image_url',
                     'image_url': {'url': f'data:image/png;base64,{b64}'}},
                ]}],
                'temperature': ring.get('temp', 1.0),
                'max_tokens': 300,
            }).encode('utf-8'),
            headers={'Content-Type': 'application/json',
                     'Authorization': f"Bearer {ring['key']}"},
            method='POST')
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        dt = (time.time() - t0) * 1000
        # 同款「HTTP 200 ≠ 成功」：body 里可能塞 base_resp.status_code
        bresp = data.get('base_resp') or {}
        if bresp.get('status_code'):
            return 'dead', f'{dt:.0f}ms · {bresp.get("status_msg") or bresp.get("status_code")}'
        content = (data.get('choices') or [{}])[0].get('message', {}).get('content') or ''
        if not content.strip():
            return 'empty', f'{dt:.0f}ms · 通道通但回复为空'
        return 'alive', f'{dt:.0f}ms · 有回复「{content.strip()[:20]}」'
    except Exception as e:  # noqa: BLE001
        dt = (time.time() - t0) * 1000
        return 'dead', f'{dt:.0f}ms · {str(e)[:150]}'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vision", action="store_true", help="探识图链（比文字链贵）")
    args = ap.parse_args()

    if args.vision:
        rings = executors._vision_rings()
        probe = _probe_vision_ring
        title = "识图链（vision）"
    else:
        rings = executors._fallback_rings_daren()
        probe = _probe_text_ring
        title = "文字链（问答 / 选品结论）"

    print(f"🔍 LLM 通道体检 · {title}")
    print("=" * 72)
    if not rings:
        print("  ❌ 没有任何环被构造出来（凭据全缺）")
        return 1

    alive, empty, dead = [], [], []
    for idx, ring in enumerate(rings):
        label = ring.get("label") or ring.get("protocol")
        model = ring.get("model")
        base = ring.get("base")
        state, detail = probe(ring, idx)
        mark = {'alive': '✅', 'empty': '⚠️', 'dead': '❌'}.get(state, '❓')
        print(f"  {mark} 环{idx} {label:22s} model={model}")
        print(f"        base={base}")
        print(f"        {detail}")
        if state == 'alive':
            alive.append((idx, label, model, detail))
        elif state == 'empty':
            empty.append((idx, label, model, detail))
        else:
            dead.append((idx, label, model, detail))

    print("=" * 72)
    if alive:
        print(f"✅ 可用环 {len(alive)}/{len(alive) + len(empty) + len(dead)} —— 生产会命中："
              f"环{alive[0][0]} {alive[0][1]}（{alive[0][2]}）")
    else:
        print("❌ 没有任何环成功返回内容 —— 问答/选品/识图会直接报错")
    if empty:
        print(f"⚠️  通道通但本次回复为空 {len(empty)} 个"
              "（如：探针图里没东西可抽 —— **不等于通道死**）")
        for idx, label, model, detail in empty:
            print(f"     环{idx} {label}（{model}）")
    if dead:
        print(f"❌ 死环 {len(dead)} 个：" + ', '.join(f'{d[0]}:{d[1]}' for d in dead))
    if not alive and not empty:
        print("（全灭 —— 所有环都返回了错误）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
