# 达人经营驾驶舱（daren_cockpit）

网页版工作台，微信风白色简约。launchd 常驻（`com.solobrave.daren-cockpit`，日志 `/tmp/daren_cockpit.*.log`）。

- **入口**：`http://192.168.1.25:7188/`（内网）
- **达人**：专属链接 `?t=<tenant_key>&k=<k>`，k 在 `cockpit_keys.json`（不入库，gitignore）
- **运营**：admin 页口令 = `cockpit_keys.json` 的 `_ops` 值

## 发版前检查清单（每次改 server.py / index.html 必过）

### 本服务
- [ ] 达人链路：正确 k → 200 + 数据；错误 k → 403；裸 `?t=` → 403
- [ ] 运营链路：admin 口令门 → roster → 下钻 `?t=`（ops 头通道）→ 改状态写回飞书 → **测完还原状态**
- [ ] 异常态：坏 JSON 400 / 非法 tenant 字符 400 / 缺 record_id 400 / 无 ops 头 403
- [ ] 落地页三卡渲染正常（JS 改动后 `node --check` 抽出的 script）
- [ ] `launchctl kickstart -k gui/$(id -u)/com.solobrave.daren-cockpit` 重启后冒烟
- [ ] `cockpit_keys.json` 未被 git 跟踪（`git status` 里不应出现）

### 关联系统回归（改动接口/鉴权后必查，2026-10-20 误报事故教训）
- [ ] **每周体检 automation**（`automation_30e3c477`，脚本在 blueprint assets）手动试跑一次 dryRun，确认 checks 全绿
- [ ] **daren_bot 读取方**：改了接口格式/鉴权/k 的存放方式时，检查 `weekly.py` / `report_card.py` 拼链接的逻辑是否还兼容（格式以 `cockpit_keys.json` 为准）
- [ ] **周报 R1/看板渲染**：改了 `/api/cockpit` 返回字段时，确认 daren_bot 侧周报和看板长图的取数字段没断

## 已知边界

- 飞书 lark-cli 只能 `--as user`；启动预热只暖 daren_boss + roster
- echarts 走 unpkg + bootcdn 兜底（jsDelivr 被墙）
- CLT sqlite 3.54 打不开 WAL 只读 URI → 用普通连接 + `PRAGMA query_only=ON`
