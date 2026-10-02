# SoloBrave 2.0 全面替代 — 功能盘点与迁移验收清单

> 基准代码：prod `07bcadd`（fix/request-level-model，含 2.0 壳层）
> 盘点方法：对 index.html 全量扫描——573 个内联 onclick / 324 个独立处理器 / 75 个 API 端点 / 63 个弹窗浮层 / 1022 个 JS 函数
> 铁律：**新设计全面替代旧设计，功能零丢失**。每模块替换须本清单 100% 勾选 + pytest 绿 + 接口 200 + 真机走查，才让旧模块 DOM 退役。
> 维护方式：每完成一项打 `[x]`，签名+日期。旧 DOM 清理单独成项，全部模块替换完后统一执行。

---

## 0. 全局设施（跨模块，最后收口但最早依赖）

- [ ] 登录 / 注册（loginOverlay 4-tab + registerOverlay 独立注册页 + 忘记密码）
- [ ] 登录后选择面板 selectionDashboard（员工/项目组 KPI 快捷入口）
- [ ] 全局搜索 globalSearchModal（员工/任务/项目三类 + 下拉快捷键）
- [ ] 通知面板 notificationPanel（列表 / 已读 / read-all）
- [ ] 用户菜单 userDropdown（个人资料 / 安全工具 / 团队管理 / 用户管理 / Token 统计 / 渠道配置 / 修改密码 / 退出）
- [ ] AI 切换器 showAISwitcher（员工切换）
- [ ] 新员工入职向导 wizardOverlay + onboardingModal + whatCanAiDoOverlay
- [ ] 右键菜单三件套：msgCtxMenu / groupCtxMenu / empSettingsMenu / contextMenu
- [ ] 高危确认：emergencyRotateModalOverlay（secret 紧急轮换）
- [ ] 移动端 ≤768px 可用（现回退旧导航，最终须新壳自持）
- [ ] 真机地址 https://192.168.1.25:8443 全功能回归

## 1. 即时通讯 / 龙虾办公室（最大模块，138 个点击处理器）

聊天主区：
- [ ] 消息流（文本 / 图片 / 聊天记录持久化 / 加载更多）
- [ ] 发送框：Enter 发送 / Shift+Enter 换行 / 图片附件 / 粘贴发送
- [ ] Slash 命令菜单 slashMenu
- [ ] @提及员工 mentionDropdown
- [ ] 模型选择 modelDropdown（请求级 model 透传，新版已废弃全局硬切）
- [ ] chatMoreDropdown（会话操作）
- [ ] 群聊：建群 / 群详情 groupDetailOverlay / 群公告 announcementModalOverlay / 加急 urgeModalOverlay / 移群 moveGroupModal / 群向导 groupWizardOverlay
- [ ] 消息右键：转发 / 删除 / 复制（msgCtxMenu）
- [ ] 聊天顶栏：AI 团队切换 / 成员数 / 网关状态
- [ ] 员工详情 empDetailOverlay（档案 / 记忆 / 技能 / 活动 / 统计大面板）
- [ ] 图片点击预览 previewImage
- [ ] 违禁词自检提示（forbidden-words）

记忆与员工维护：
- [ ] 记忆压缩 compressModal / 扫描 scanModal / 重置 resetModal
- [ ] 核心候选记忆 coreCandidateModalOverlay / 冲突处理 memoryConflictModalOverlay
- [ ] AI 设置面板 aiSettingsPanel / 自定义 API customApiPanel
- [ ] execApprovalModal（执行审批）

接口：/api/chat/、/api/chat/summarize/、/api/agents、/api/memory/、/api/memory/consolidate、/api/openclaw/dreaming、/api/vision/describe、/api/forbidden-words(/check)、/api/team-feed/、/api/notification-settings、/api/account

## 2. 知识库（12 个端点 + 编辑器全家桶）

- [ ] 三栏：分类侧栏 knowledgeMid（分类管理 + 右键 knowledgeCatContextMenu）/ 列表 / 详情
- [ ] 视图切换 knowledgeViewPanel（三视图，dense-views 已做一半）
- [ ] 编辑器 knowledgeEditPanel（含版本历史 knowledgeVersionModalOverlay）
- [ ] 知识详情右滑 knowledgeRight + knowledgeRightOverlay
- [ ] 新建/编辑弹窗 knowledgeModalOverlay / knowledgeDocModalOverlay
- [ ] 语义搜索 /api/knowledge/search（已切新表 rag_retrieve_kb）
- [ ] 大脑知识 /api/brain/knowledge/ + /api/brain/status + /api/brain/trigger-manual
- [ ] RAG 重建 /api/rag/retrieve、/api/rag/build
- [ ] 分类接口 /api/knowledge/categories(/:id)
- [ ] 新表 CRUD /api/knowledge/entries(/:id)（kb_entries）
- [ ] 知识事件 /api/knowledge-events/
- [ ] scope 四层隔离展示（全员/个人/团队/项目组）

## 3. 规律库（64 个点击处理器，前端最重的模块）

- [ ] 三栏：类目侧栏 patterns-cat-sidebar / 规律列表 / 详情
- [ ] 规律卡：📐/🔬/🧪 分级 / 置信度 / 命中数 / evidence 进度条
- [ ] 反馈投票（有用/无用，pattern-fb-btn）
- [ ] 手动归纳 patternInduceModalOverlay + /api/knowledge-patterns/induce
- [ ] 归纳结果 inductResultModalOverlay
- [ ] CRUD /api/knowledge-patterns(/:id)
- [ ] 晋升链可视化（evidence ≥30 可晋升）
- [ ] deal 合作记录 dealModalOverlay + /api/deals(/:id)

## 4. 达人库

- [ ] Tab 分层（主库 / 已分析 / 演示数据开关）
- [ ] 录入/编辑 talentModalOverlay + OCR 识别落库
- [ ] 详情页（重设计版，09-29 收口）
- [ ] 跟进记录 talentFollowUpModalOverlay + /api/talents/:id/follow-ups
- [ ] 筛选：类目 / 评级 / 合作状态 / 搜索
- [ ] 匹配入口 matchModalOverlay（调商品匹配链）
- [ ] 飞书同步 /api/talents/sync-feishu
- [ ] 注入文本 /api/talents/injection-text
- [ ] 权限隔离：非管理员仅见自己子库（两层架构）

## 5. 商品库

- [ ] 列表 / 录入 / 编辑 productModalOverlay + productsRightOverlay
- [ ] 飞书同步 /api/products/sync-feishu
- [ ] 匹配：/api/match/product-to-influencer、/api/match/influencer-to-product、/api/products/:id/score（V3 评分 S/A/B/C/D）
- [ ] 品牌 /api/brands
- [ ] 商品-达人联动看板 /api/dashboard/linkage

## 6. 任务

- [ ] 列表 / 新建 / 编辑 / 状态流转（待办/进行中/完成）/api/tasks(/:id)
- [ ] 按员工/项目过滤

## 7. 设置

- [ ] settingsMid 全项：模型配置 / embedding 配置 / 渠道配置 channelConfigModal / 通知 / 安全
- [ ] /api/permissions(/modules /roles/:id) 角色权限矩阵
- [ ] /api/user/feishu-config 飞书绑定
- [ ] OpenClaw 技能：/api/openclaw/skills(list/install/remove)
- [ ] 员工模板 /api/employee-templates
- [ ] 积分：/api/credits/balance|quotas|recharge + /api/token-usage/sync
- [ ] 用户管理：userEditOverlay / createUserOverlay（admin）
- [ ] 团队管理：createTeamOverlay / editTeamOverlay / teamMembersOverlay + /api/teams(/:id)
- [ ] 项目组：/api/groups(/:id)

## 8. 工作台（2.0 新增，已上线 ✅）

- [x] KPI 四卡真实值（GMV 汇总/达人数/kb ok 数/员工在线）
- [x] 今日关注 + 数据速览（真实数据组装）
- [x] 60px 图标轨 + 上下文侧栏 + 旧 left-nav 兜底
- [ ] 暗色模式切换入口（token 已预埋）
- [ ] 接入 ⌘K 命令面板（第 3 步）

## 9. 迁移完成后统一收口

- [ ] 旧 left-nav / 旧模块隐藏 DOM 全量清理
- [ ] `--sb2-*` token 转正（去命名空间或全量替换旧变量）
- [ ] index.html 体积审计（目标：替代后净增长可控，旧 CSS 全清）
- [ ] 4 个旧知识表读取点随旧面板退役一并灰度归档（见 03-backlog）

---

## 执行顺序（锁定）

壳层 ✅ → dense-views（妍妍在途）→ **聊天（本清单 §1，最大）** → 知识库+规律库 → 达人库+商品库+任务 → 设置+全局设施+⌘K → 暗色 → §9 收口

每步：独立 worktree 分支 → 对照本清单勾选 → py_compile + pytest（基线 316）+ 备用端口真机 → 交妍妍走查 → ff merge → 部署 kickstart → 真机 8443 回归
