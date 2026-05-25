# OpenClaw 更新监控智能体 - 需求文档

## 背景
OpenClaw 是一个持续更新的开源项目，用户需要及时了解最新版本变化，确保本地安装的版本不过时。

## 目标
创建一个智能体，能够：
1. 主动检查 OpenClaw 最新版本
2. 对比用户本地安装版本
3. 实时报告更新内容和升级建议
4. 学习 OpenClaw 技术文档，提供技术支持

## 功能需求

### P0 - 版本检查与报告
- [ ] 定期（每日/每周）检查 OpenClaw GitHub 仓库最新 release
- [ ] 获取 release notes 和变更日志
- [ ] 对比用户本地版本（通过 `claw --version` 或 `pip show openclaw`）
- [ ] 发现新版本时主动推送通知给用户
- [ ] 生成更新摘要：新增功能、修复的 bug、安全更新等

### P1 - 智能升级建议
- [ ] 分析更新内容对用户现有工作流的影响
- [ ] 判断是否为安全更新（建议立即升级）或功能更新（可延后）
- [ ] 提供升级命令和步骤
- [ ] 记录用户升级历史到 MEMORY.md

### P2 - 技术文档学习
- [ ] 学习 Datawhale《Hello Claw》教程
- [ ] 学习 OpenClaw 官方文档
- [ ] 能够回答用户关于 OpenClaw 使用的问题
- [ ] 能够诊断 OpenClaw 相关 bug

## 技术实现

### 版本检查方式
```bash
# 方式1：GitHub API 查询最新 release
curl -s https://api.github.com/repos/openclaw/openclaw/releases/latest | jq -r '.tag_name'

# 方式2：PyPI 查询最新版本
curl -s https://pypi.org/pypi/openclaw/json | jq -r '.info.version'

# 方式3：本地版本检查
claw --version
# 或
pip show openclaw | grep Version
```

### 通知方式
- 通过 CoPaw 频道消息推送
- 在聊天界面显示系统通知
- 记录到 MEMORY.md

### 存储
- 记录最后检查时间
- 记录已知最新版本
- 记录用户本地版本
- 记录更新历史

## 参考：Trumind 实现方式

Trumind 的做法：
1. 主动查询 OpenClaw 最新版本（v2026.5.20）
2. 对比上次已知版本（v2026.5.12）
3. 列出主要更新内容（Exec approvals、Discord、Gateway、Agents、Control UI）
4. 询问用户本地版本，以便判断是否需要更新
5. 建议用户运行 `claw --version` 获取本地版本

## 与 SoloBrave 的集成

### 场景1：用户询问 OpenClaw 更新
用户："OpenClaw 有更新吗？"
智能体：检查 → 对比 → 报告更新内容 → 建议升级

### 场景2：定期自动检查
每天定时检查，发现新版本时主动推送：
"OpenClaw 有新版本 v2026.5.20，主要更新：... 你当前版本是 v2026.5.12，建议升级。"

### 场景3：技术支持
用户遇到 OpenClaw 相关问题，智能体基于学习的技术文档提供解答。

## 后续工单

1. **S2-1**: OpenClaw 版本检查 API 封装
2. **S2-2**: 版本对比与更新摘要生成
3. **S2-3**: 主动通知机制（定时任务）
4. **S2-4**: 技术文档学习与问答系统
