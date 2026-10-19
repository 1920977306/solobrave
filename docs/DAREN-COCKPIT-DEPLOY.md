# 达人经营驾驶舱 · 内网服务器部署手册

> 目标机：192.168.1.25（跑全可AI Office 的那台 Linux 主机）
> 端口：**7188**（避开 8080 solobrave / 8443·18210 AI Office）
> 前提：本机（Mac）无 SSH 权限，部署由服务器上的 agent 执行；代码经 GitHub 同步。

## 0. 执行者与环境自检

```bash
cd <服务器上的 solobrave-prod 目录>
git fetch origin && git log origin/main -1 --oneline   # 应 >= 8c25ada（驾驶舱）+ 最新快照修复
python3 --version                                      # >= 3.9 即可（纯 stdlib）
ls daren_cockpit/                                      # index.html package.json server.py
```

**数据依赖三样**（服务器上缺一样就少一块数据，页面不崩、对应区块显示"暂无"）：
1. `daren_bot` 包 + `data/daren-bot/daren_bot.db` → 缺了报 500（租户查询在请求路径上，必须存在）
2. `data/solobrave.db` → 缺了选品/档案兜底为空
3. `lark-cli` 已 `--as user` 登录（全可传媒小翊）→ 缺了飞书表格数据（选品结论/互动/合作机会）为空

自检：
```bash
python3 -c "import sys; sys.path.insert(0,'.'); from daren_bot import config, db as botdb; print(botdb.get_tenant('daren_boss')['bind_douyin_id'])"
which lark-cli && lark-cli auth status --as user 2>&1 | head -3
```

## 1. 启动与自启（systemd）

```ini
# /etc/systemd/system/daren-cockpit.service
[Unit]
Description=Daren Cockpit (wechat-style dashboard)
After=network.target

[Service]
WorkingDirectory=<服务器 solobrave-prod 绝对路径>
ExecStart=/usr/bin/python3 daren_cockpit/server.py --host 0.0.0.0 --port 7188
Restart=always
RestartSec=5
# 非 root 运行：
User=<运行 solobrave 的同名用户>

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now daren-cockpit
systemctl status daren-cockpit
```

注意：`--host 0.0.0.0` 是内网手机能访问的关键；不要挂到公网（无鉴权，纯内测）。

## 2. 验收（服务器上跑）

```bash
curl -s "http://127.0.0.1:7188/api/cockpit?t=daren_boss" | python3 -m json.tool | head -20
# 关键看：name/fans/gmv_text/cards 非空；首次 lark-cli 调用约 1-2s 属正常
```

## 3. 老大手机访问

- 手机连家里 WiFi（192.168.1.x），浏览器开 `http://192.168.1.25:7188/`
- 多租户：URL 加 `?t=<tenant_key>`，默认 daren_boss

## 4. 运维

- **日志**：`journalctl -u daren-cockpit -f`（server.py 的 API 错误会打到这里）
- **更新**：Mac 侧提交 push → 服务器 `git pull` → `systemctl restart daren-cockpit`（TEMPLATE 在启动时读入内存，必须重启才生效）
- **数据源 freshness**：daren_bot.db / solobrave.db 是 Mac 侧每周采集更新的。服务器若要展示最新数据，需定期 rsync（建议每周一采集后：`rsync -avz --exclude='*.db-wal' mac:data/  server:data/`），或在服务器侧直接跑采集（另议，内测期先 rsync）
- **防火墙**：默认放通；如开了 ufw：`sudo ufw allow from 192.168.1.0/24 to any port 7188`

## 5. 回滚

```bash
git checkout <上一个 good commit> -- daren_cockpit daren_bot/bitable_sync.py
sudo systemctl restart daren-cockpit
```
