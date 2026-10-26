#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MiniMax **Code 订阅**凭据的**唯一事实来源**。

★ 为什么单独成文件（2026-10-27 新增）：
  同一份「实时读盘取 OAuth accessToken」的逻辑，此时有**两处**真实需求：

  ① `daren_bot/llm_channels.py` —— 对话 / 问答 / 采集摘要链的「环 1（= 贾维斯自己）」
  ② `solobrave-server.py`      —— 代理 ProviderChain 的兜底档（救木木那条）

  两处各写一份 = 又一次「同源同 bug」（本项目的老教训）⇒ **收敛成这一份**。
  第三方调用方（llm_channels / solobrave-server）都从这里取，
  改「去哪儿读、怎么判过期」只需改一处。

──────────────────── 设计约束（改动前必读）────────────────────

1. **绝不缓存 token**（AGENTS.md §7「第三种密钥形态：客户端自持的凭据」）：
   token 是 30 分钟量级的短期凭据，Code 桌面端会自行续期并**重写 auth.json**
   （旧 token 随之作废）。缓存下来 ⇒ 下一次调用必 401 ⇒
   表现是「刚开始还能用、跑一会儿全灭」，极易被误判成「额度又没了」。

2. **绝不复制进 .env / 代码 / secret store**：
   客户端自持的凭据一旦被复制，就多出一份「需要轮换的副本」，
   而客户端续期**不会**同步到那份副本 ⇒ 那份副本必然过期 ⇒ 变成定时炸弹。

3. **过期就跳过**：剩余不足 60s 视为过期 ——
   宁可少一个环，也别让用户等一次注定 401 的请求。

4. **不落任何密钥明文**：本模块只提供「去哪儿读」的路径与读取逻辑。

5. **鉴权形态不可猜**：Code 订阅端点（agent.minimax.cn）**只认**
   `Authorization: Bearer <token>`；实测发 `x-api-key` 会得到
   `401 {"code":401,"message":"token is required"}`（2026-10-27 实测）。
   ⇒ 协议/鉴权形态由调用方的适配器负责，本模块只管「凭据从哪儿来」。
"""
import json
import time
from pathlib import Path

#: Code 桌面端凭据根目录（auth.json 落在 <根>/<env>/<region>/mcode-public/auth.json）
CODE_SUB_AUTH_DIR = Path.home() / '.minimax' / 'auth'

#: Code 订阅端点（Anthropic 协议，path = /messages）
CODE_SUB_BASE = 'https://agent.minimax.cn/mavis/api/v1/llm/v1'

#: 代理 ProviderChain 配置里用来**占位**本凭据的标记。
#:
#: ⚠️ 为什么用标记而不是把 token 写进 settings.json：
#:   token 30 分钟就过期 ⇒ 写进去就是「一份必然过期的副本」（见设计约束 2）。
#:   ⇒ 配置里只放这个**标记**，运行时由调用方**实时读盘**换成真 token。
CODE_SUB_TOKEN_MARKER = '__CODE_SUB_TOKEN__'

#: Code OAuth accessToken 的前缀（非它的一律不是 Code 订阅凭据）
CODE_SUB_TOKEN_PREFIX = 'mmoat_'

#: 剩余有效期下限（毫秒）：不足这个数就当作过期，别让用户等一次注定 401 的请求
CODE_SUB_MIN_TTL_MS = 60_000


def code_sub_token(auth_dir=None) -> str:
    """取 MiniMax Code 订阅的 OAuth accessToken（**每次调用实时读盘，绝不缓存**）。

    `auth_dir` 允许调用方传自己的目录（便于单测用假凭据盘）；
    默认读 `CODE_SUB_AUTH_DIR`。

    返回 '' 表示「当前没有可用的 Code 订阅凭据」
    （Code 桌面端未登录 / 凭据已过期 / 磁盘读失败）—— 调用方应**跳过这一环**，
    而不是拿空 token 去发一次注定失败的请求。
    """
    now_ms = time.time() * 1000
    base = Path(auth_dir) if auth_dir else CODE_SUB_AUTH_DIR
    try:
        auth_files = sorted(base.glob('**/mcode-public/auth.json'))
    except Exception:
        return ''
    best = ''
    for auth_file in auth_files:
        try:
            with open(auth_file, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        for rec in (data.get('records') or {}).values():
            if not isinstance(rec, dict):
                continue
            tok = rec.get('accessToken') or ''
            if not tok.startswith(CODE_SUB_TOKEN_PREFIX):
                continue
            exp = rec.get('expiresAtMs') or 0
            # 剩余不足 60s 视为过期 —— 宁可跳过这一环，也别让用户等一次 401
            if exp and exp - now_ms < CODE_SUB_MIN_TTL_MS:
                continue
            if len(tok) > len(best):
                best = tok
    return best
