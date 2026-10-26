#!/usr/bin/env python3
"""LLM 通道体检 —— 逐环真调一次，看现在**到底哪个能用**。

背景（2026-10-22 老大）：「kimi 现在没额度，只能你来」。
项目的问答/选品/识图都走**降级环**（套餐 API → Code 订阅 OAuth → kimi → minimax 开放平台），
所以「Kimi 没额度」本身**不致命** —— 只要环 0/环 1 通，用户就无感。
但「哪个环在顶」必须**实测**才知道，光看代码里的注释（写着 kimi 429 / minimax 402）
是 2026-10-21 的快照，今天可能又变了。

本脚本**逐环独立发一次真实请求**（不走降级逻辑，所以能看清每一环单独的死活），
输出每环的 HTTP 结果 / 耗时 / 错误摘要，并给出「当前能顶上的是哪一环」。

★ 2026-10-25 新增两个维度（回答「要不要再买 API」的真判据）：
  - **独立服务商**（`vendor`）：「一家挂了是不是全家死」。
    改造前链上 3 个「可用环」实则**全是 MiniMax 一家** —— 冗余是假的。
  - **独立额度池**（`pool`）：「还剩几种能顶的额度」。
    套餐 key 与开放平台 key 同属 MiniMax 但是**两套独立的账**，
    开放平台 402 **不影响**套餐继续用 ⇒ 与 vendor 是两个问题，不能互相替代。
  ⚠️ 两个字段都由 `llm_channels` 构造环时**写死**，探针只读不算 ——
     从 host 反推会把同一家数成三家。

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


def _vendor_of(ring: dict) -> str:
    """环的服务商归属。

    ★ 为什么要显式字段而不是从 base 反推 host：
      `api.minimax.cn`（套餐 API）/ `agent.minimax.cn`（Code 订阅）/
      `api.minimaxi.com`（同一把 key 的另一条协议）**是同一家**，
      但 host 三个都不一样 —— 反推 host 会把一家数成三家，
      结论就是「有 3 家兜底，出事不慌」，而实际**全是同一家**。
      ⇒ `vendor` 由 `llm_channels` 构造环时写死，探针只读不算。
    """
    return ring.get('vendor') or 'unknown'


def _pool_of(ring: dict) -> str:
    """环的额度池归属 —— 「同一家」≠「同一个池」。

    套餐 CP key 与开放平台 key 都是 MiniMax，但是**两套独立的账**
    （订阅额度 vs 账户余额）：开放平台 402 不影响套餐订阅继续用。
    ⇒ 「要不要买第二家」看 vendor；「还剩几种能顶的额度」看 pool。
    """
    return ring.get('pool') or 'unknown'


def _classify_dead(detail: str) -> str:
    """死因归类 —— 「额度型死」和「故障型死」要分开看。

    额度型（买/续费能救）：429 月度额度耗尽、402 余额不足、1113 无可用资源包
    故障型（重试/换家能救）：5xx、超时、协议错、401 鉴权错
    """
    d = detail.lower()
    if '429' in d or '402' in d or '1113' in d or 'quota' in d or 'balance' in d:
        return '额度型（买/续费可救）'
    if '401' in d or '403' in d:
        return '鉴权型（key 过期/无效）'
    if '500' in d or '502' in d or '503' in d or '504' in d or 'timeout' in d:
        return '故障型（重试可救）'
    return '其他'


def _ring_of(item):
    """从 alive/empty/dead 的**条目**里取 provider 描述（ring dict）。

    ⚠️★ 2026-10-27 实锤的**真 bug**（此前每次跑这个探针都崩）：
       真实调用方 `main()` 存的是 **(ring, detail) 元组**，
       而历史单测直接传 **ring dict** ⇒ 两种形状都得认。
       以前这里只认 dict ⇒ 真调用 `_vendor_of((ring, detail))` →
       `tuple.get(...)` → **AttributeError** ⇒ 探针**每次跑到最后一行必崩**：
         ① 退出码恒为 1（哪怕有活环）⇒ 谁拿它当闸门都会误判；
         ② 最关键的「活着的独立服务商 / 额度池」这段结论**从来没打印过**。
       ⇒ 单测之所以绿，是因为**单测传的形状和真实调用方不一样**
         （典型的「测试形状 ≠ 生产形状」）。
    """
    if isinstance(item, dict):
        return item
    if isinstance(item, (tuple, list)) and item:
        first = item[0]
        if isinstance(first, dict):
            return first
    return {}


def _hit_ring_summary(rings: list, alive: list, total: int = 0) -> str:
    """生成「生产会命中：环N <label>（<model>）」那一行。

    ★ 环号必须是**真实下标**。原来硬编码「环0」，实际命中的是 alive[0] 那一档
      —— 实测输出「生产会命中：环0 code-sub」，而 code-sub 其实是**环1**
      ⇒ 运维照着「环0」去查会查错对象（把真命中的当没命中的查）。
      ⇒ 抽成独立函数是为了**能单测**：留在 print 里就只能靠肉眼盯。
    """
    if not alive:
        return '❌ 没有任何环成功返回内容 —— 问答/选品/识图会直接报错'
    first = _ring_of(alive[0])
    first_idx = next((i for i, r in enumerate(rings or []) if r is first), 0)
    n_alive = len(alive)
    total = total or n_alive
    return (f'✅ 可用环 {n_alive}/{total} —— 生产会命中：'
            f'环{first_idx} {first.get("label")}（{first.get("model")}）')


def _independence(rings: list, alive: list, empty: list, dead: list) -> dict:
    """独立服务商 / 独立额度池统计（**纯函数**，可被测试直接调用）。

    ★ 两个维度回答两个不同问题，混用必错：
      `vendor` → 「一家挂了是不是全家死」= 判「要不要买第二家 API」
      `pool`   → 「还剩几种能顶的额度」（同家的不同账/不同协议也算不同池）

    ⚠️ `empty`（通道通但这次没吐字）**必须算进活的**：
       「活着但没出结果」≠「死了」—— 判成死会把活的池误报成死的
       （识图链探针拿 1×1 图去探，模型诚实返回空是**正确行为**）。
    ⚠️ 合并逻辑放在**函数内部**而不是调用方 ——
       放外面的话，调用方漏传 empty 时测试抓不到（恒真闸门）。
    ⚠️ 入参形状兼容：条目可以是 ring dict，也可以是 (ring, detail) 元组
       （真实调用方用的就是元组，见 `_ring_of` 的说明）。
    """
    alive = [_ring_of(x) for x in (alive or [])]
    empty = [_ring_of(x) for x in (empty or [])]
    dead = [_ring_of(x) for x in (dead or [])]
    live = alive + empty
    vendors = sorted({_vendor_of(r) for r in live})
    pools = sorted({_pool_of(r) for r in live})
    dead_pools = sorted({_pool_of(r) for r in dead})
    return {
        'all_vendors': sorted({_vendor_of(r) for r in (rings or [])}),
        'alive_vendors': vendors,
        'alive_pools': pools,
        'dead_pools': dead_pools,
        'vendor_redundant': len(vendors) >= 2,
    }


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
        vendor = _vendor_of(ring)
        pool = _pool_of(ring)
        state, detail = probe(ring, idx)
        mark = {'alive': '✅', 'empty': '⚠️', 'dead': '❌'}.get(state, '❓')
        print(f"  {mark} 环{idx} {label:22s} model={model}")
        print(f"        base={base}")
        print(f"        服务商={vendor}  额度池={pool}")
        print(f"        {detail}")
        if state == 'alive':
            alive.append((ring, detail))
        elif state == 'empty':
            empty.append((ring, detail))
        else:
            dead.append((ring, detail))

    print("=" * 72)
    total = len(alive) + len(empty) + len(dead)
    print(_hit_ring_summary(rings, alive, total))
    if empty:
        print(f"⚠️  通道通但本次回复为空 {len(empty)} 个"
              "（如：探针图里没东西可抽 —— **不等于通道死**）")
        for ring, _d in empty:
            print(f"     {ring.get('label')}（{ring.get('model')}）")
    if dead:
        print(f"❌ 死环 {len(dead)} 个：" + ', '.join(
            f'{r.get("label")}[{_pool_of(r)}]' for r, _d in dead))
        by_reason = {}
        for ring, detail in dead:
            by_reason.setdefault(_classify_dead(detail), []).append(ring.get('label'))
        for reason, items in by_reason.items():
            print(f"     · {reason}: {', '.join(items)}")

    # ★★ 两个维度，两个问题（2026-10-25 实测教训）
    stat = _independence(rings, alive, empty, dead)
    print("-" * 72)
    print(f"🏢 **活着的独立服务商 {len(stat['alive_vendors'])} 家**："
          f"{', '.join(stat['alive_vendors']) or '（无）'}")
    print(f"💧 **活着的独立额度池 {len(stat['alive_pools'])} 个**："
          f"{', '.join(stat['alive_pools']) or '（无）'}")
    if stat['dead_pools']:
        print(f"💀 死的额度池 {len(stat['dead_pools'])} 个：{', '.join(stat['dead_pools'])}")
    if stat['vendor_redundant']:
        print("⇒ 服务商级冗余**真实**：一家挂了还有别家顶上。")
    elif stat['alive_vendors']:
        print("⇒ ⚠️ 服务商级冗余**是假的**：所有活环都来自同一家 —— 这家挂了全线崩。")
    else:
        print("⇒ ❌ 无任何可用服务商。")
    if len(stat['alive_pools']) <= len(stat['alive_vendors']):
        print("⇒ 每家都只占一个池 —— 额度层面没有额外冗余。")
    else:
        print("⇒ 额度层面比服务商层面多几层（同家的不同账/不同协议）。")

    if not alive and not empty:
        print("（全灭 —— 所有环都返回了错误）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
