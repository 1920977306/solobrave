# 达人经营驾驶舱 · 部署手册（已落地版）

> **关键事实（2026-10-19 部署时纠正）**：192.168.1.25 **就是这台 Mac**——不存在 SSH/远程问题，数据全在本地（无需 rsync），常驻用 **launchd**（不是 systemd）。

## 现状

- 服务：`http://192.168.1.25:7188`（0.0.0.0 监听，内网手机可访问）
- 常驻：`launchctl list | grep daren-cockpit` → `com.solobrave.daren-cockpit`
- plist：`~/Library/LaunchAgents/com.solobrave.daren-cockpit.plist`（KeepAlive + RunAtLoad，日志 `/tmp/daren_cockpit.out.log` / `.err.log`）
- 解释器：CLT python3.9（`/Library/Developer/CommandLineTools/usr/bin/python3`，纯 stdlib）

## launchd 环境的两个坑（已在代码层修复，勿回退）

1. **sqlite `mode=ro` URI 打不开 WAL 库**：CLT 自带 sqlite 3.54 对 WAL 库的只读 URI 打开直接报错（venv 的 3.53 没事，所以本机测试发现不了）。
   修复：`daren_cockpit/server.py` 用普通连接 + `PRAGMA query_only=ON`（引擎级禁写，比 ro URI 更强）。
2. **launchd 极简 PATH 找不到 lark-cli**（在 /opt/homebrew/bin）：症状是飞书表格数据全空（选品/互动/合作/趋势），页面不报错。
   修复：`_lark_cli()` 绝对路径兜底 + subprocess 显式注入 PATH。

**改代码后必须重启服务**（TEMPLATE 启动时读入内存）：
```bash
launchctl kickstart -k gui/$(id -u)/com.solobrave.daren-cockpit
```

## 验收

```bash
curl -s "http://192.168.1.25:7188/api/cockpit?t=daren_boss" | python3 -m json.tool | head -20
# 健康标准：name=真实昵称（非租户key）、fans>0、verdict≠评估中、cards 非空
# ⚠️ 只 curl 根路径/HTTP 200 不算验收——launchd 空数据时也是 200
```

## 运维

- 日志：`tail -f /tmp/daren_cockpit.err.log`
- 更新：本仓库提交后 `git pull`（同一台机器）→ `launchctl kickstart -k gui/$(id -u)/com.solobrave.daren-cockpit`
- 手机访问：连家里 WiFi → `http://192.168.1.25:7188/`，多租户加 `?t=<tenant_key>`（默认 daren_boss）
- 回滚：`git checkout <commit> -- daren_cockpit` → 重启服务

## 数据新鲜度

无 rsync 问题——驾驶舱读的就是本仓库 `data/` 下的库，机器人（`com.solobrave.daren-bot`）每周采集写入后，驾驶舱 120s 缓存过期即见新数据。
