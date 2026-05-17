# S1后续任务工单 — 妍妍

## 前置条件
1. 先 `git pull origin dev` 拉取最新代码（包含bugfix）
2. 阅读 `docs/S1-bugfix-notice-妍妍.md`，确认理解编码规范

## 任务1：验证S1技能系统联调 ⚡优先

服务器已重启（dev分支），需要验证以下功能：

### 前端验证步骤
1. 用 admin/admin123 登录 http://192.168.1.25:8080
2. 点击任意员工 → 右侧抽屉 → "技能"Tab
3. 验证两个区域都显示：🛠️ OpenClaw技能 + 🏷️ 自定义标签
4. 在搜索框输入关键词 → 点击搜索 → 验证搜索结果展示
5. 点击"安装"按钮 → 验证安装成功提示 + 技能出现在已安装列表
6. 点击技能的"卸载"按钮 → 验证卸载成功
7. 点击"新增员工"向导 → Step3 → 验证技能选择区域显示6个热门技能卡片
8. 选中2-3个技能 → 完成创建 → 验证技能自动安装

### 后端验证（如需要）
```bash
# 列出已安装技能
curl -H "Authorization: Bearer YOUR_TOKEN" http://192.168.1.25:8080/api/openclaw/skills/list

# 搜索技能
curl -H "Authorization: Bearer YOUR_TOKEN" "http://192.168.1.25:8080/api/openclaw/skills/search?q=search"

# 安装技能
curl -X POST -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \
  -d '{"skillName":"web-search"}' http://192.168.1.25:8080/api/openclaw/skills/install

# 卸载技能（注意：需确认是 skill remove 还是 skill uninstall）
curl -X POST -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \
  -d '{"skillName":"web-search"}' http://192.168.1.25:8080/api/openclaw/skills/remove
```

### 如发现问题
直接在代码里修复，遵守编码规范，推到dev分支。

---

## 任务2：确认OpenClaw CLI卸载命令

当前后端用的是 `openclaw skill remove`，但需要确认正确的CLI命令：

在Mac mini上测试：
```bash
openclaw skill --help
```
看输出里是 `remove` 还是 `uninstall`。如果是 `uninstall`，需要修改 `solobrave-server.py` 中对应的命令。

---

## 任务3：Dreaming（睡梦）功能开关

### 需求
在员工详情面板的"连接"Tab中添加Dreaming开关，不用去OpenClaw官方UI操作。

### 技术方案
- Dreaming是OpenClaw的Agent配置，存储在 `~/.openclaw/openclaw.json`
- 控制方式：`openclaw config.patch` RPC方法，或 `/dreaming on|off|status` 命令
- 三阶段：Light（浅睡，偶尔检查）→ REM（快速眼动，主动学习）→ Deep（深睡，全量蒸馏）

### 实现步骤
1. 后端：添加 `/api/openclaw/dreaming/status` 和 `/api/openclaw/dreaming/toggle` API
2. 前端：员工详情"连接"Tab，Gateway状态下方添加Dreaming开关行
3. 开关样式：Apple风格toggle switch，显示当前阶段

### 参考代码位置
- 连接Tab：搜索 `empDetailGroup` 或 `emp-tab-connect`
- Gateway状态行：已有 `updateGatewayStatus()` 函数

---

## ⚠️ 重要提醒
- 所有代码推到 **dev** 分支，不推 main
- 遵守编码规范（见 docs/S1-bugfix-notice-妍妍.md）
- 提交前做语法检查
- onclick引号必须用 `\'` 转义
