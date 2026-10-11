# daren-bot — 达人飞书机器人（独立进程）

技术方案见仓库根目录 `DAREN-BOT-SPEC.md`（v1.1，内测长连接模式）。
本目录是服务实现。独立 SQLite 库 + 一租户一条飞书长连接 + 异步 worker。

## 环境

```bash
python3 -m venv .venv-daren          # 仓库根目录执行（已建好）
.venv-daren/bin/pip install -r daren_bot/requirements.txt
```

## 运行

```bash
.venv-daren/bin/python -m daren_bot.main init-db
.venv-daren/bin/python -m daren_bot.main add-tenant <tenant_key> <app_id> <app_secret> [douyin_id]
.venv-daren/bin/python -m daren_bot.main serve
```

- 健康检查：`http://127.0.0.1:8092/healthz`
- 数据：`data/daren-bot/`（daren_bot.db、master.key、images/）
- 主密钥 `master.key` 0600，首启自动生成；app_secret 经 AES-GCM 加密落库。

## P0 验证（spec 第 1/8 章）

1. `add-tenant` 入库 + `serve` 起进程（日志出现 `ws client starting`）
2. 飞书开放平台该应用 → 事件订阅 → 选「使用长连接接收事件」→ 添加
   `im.message.receive_v1` → 保存（**保存时 client 必须已在线**，否则平台报错）
3. 给机器人发测试消息 → **10 秒内**日志出现 `im.message.receive_v1` 即通过
4. kill 进程重拉 → 补发消息仍能收到 = 断线重连不丢

## 当前边界（内测骨架）

- 免费路由 + 余额/限频 + 预扣/入队/确认卡片：**已接**
- worker 失败 → 自动返还积分 → 推送告知：**已接**
- 浏览器采集 / vision / 意图分类 / 卡片消息格式 / Bitable 建表 / KB 回流：**未接**
  （对应 executor 抛 NotImplementedError，走失败返还路径，不产生静默扣费）
- 无关问题零成本软话术拒答：**已接**（分类器接入前一律软话术，fail-closed）
