# 阶段 2 对账：[tenant] 91 处 is_admin → tenant_owner 映射表（2026-11-09）

> 生成：`scripts/label_is_admin_sites.py` 全量扫描 + 人工逐函数归桶。改代码后行号会漂，重跑脚本。
> 本文档**纯对账，不含代码改动**。阶段 2 每批动手前，老大按 §4 决策点拍板。

## 1. tenant_owner 定义（阶段 2 新角色）

| 项 | 口径 |
|---|---|
| 业务含义 | 合伙人/代理商：管自己拉来的达人和本租户业务，**不碰积分**（批 2 铁律已锁） |
| kind | `subjects.kind = 'tenant_owner'`（新增 kind 值） |
| 读法 | 新增 `AuthResult.is_tenant_owner` property：kind 行优先，回退 `role=='tenant_admin'`（存量无此角色→False，同 is_tenant_admin 现状） |
| 并联原则 | 目标形态统一 `auth.is_platform_admin or auth.is_tenant_owner`——平台超管是超集，tenant_owner 只管本租户 |
| 数据隔离 | 阶段 2 只做**角色身份**（谁能管），行级租户隔离（数据带 tenant_id）归阶段 3；单租户期 tenant_owner 的"本租户"≈ 全量 |

## 2. 四桶分类汇总

**[tenant] 91 处消费 = A 40 + B 37 + C 12 + D 2**

| 桶 | 目标语义 | 处数 | 执行批次 |
|---|---|---|---|
| A | 本租户业务管理面：员工/任务/商品/品牌/交易/达人 + 达人注入可见性 → `is_platform_admin or is_tenant_owner` | 40 | 阶段2批1 |
| B | 知识库 scope 层：`is_admin=` 传参进 ks.can_*/rag/记忆/搜索 → ks 层加"租户管理可见"维度 | 37 | 阶段2批2（最大块，ks 服务层配合） |
| C | 平台维护面：索引重建/修复/清理/批量重试/用量统计/规律库 → 切 `is_platform_admin`（tenant_owner 不给） | 12 | 阶段2批0 |
| D | 框架/锚点：is_admin property 本体、权限计算输入、默认形参 | 2 | 不动 |

## 3A. A · 业务管理面 → tenant_owner 并联（40 处）

| 行号 | 函数 | 现状代码 | 目标语义 | 改法 |
|---|---|---|---|---|
| :2873 | `_check_talent_write_permission` | `if auth.is_admin and not getattr(auth, 'localhost_agent_id', None):` | tenant_owner 可写本租户达人 | 并联 |
| :3186 | `_get_accessible_agent_ids` | `if auth.is_admin:` | tenant_owner 可见本租户员工 | 并联 |
| :13492 | `_handle_get_agents` | `if auth.is_admin:` | tenant_owner 管本租户员工 | 并联 |
| :13572 | `_handle_get_agent` | `if not auth.is_admin:` | tenant_owner 可读 | 并联 |
| :13627 | `_handle_get_agent_stats` | `if not auth.is_admin:` | tenant_owner 看本租户统计 | 并联(2处) |
| :13633 | `_handle_get_agent_stats` | `is_scope_filtered = not (auth.is_admin and not getattr(auth, 'localhost_agent_id', None))` | tenant_owner 看本租户统计 | 并联(2处) |
| :13755 | `_handle_get_agent_activity` | `if not auth.is_admin:` | tenant_owner 看本租户活动 | 并联(2处) |
| :13760 | `_handle_get_agent_activity` | `is_scope_filtered = not (auth.is_admin and not getattr(auth, 'localhost_agent_id', None))` | tenant_owner 看本租户活动 | 并联(2处) |
| :14029 | `_handle_create_agent_inner` | `if not auth.is_admin:` | tenant_owner 可建 | 并联 |
| :14170 | `_handle_update_agent` | `if not auth.is_admin:` | tenant_owner 可改 | 并联 |
| :14372 | `_handle_delete_agent` | `if not auth.is_admin:` | tenant_owner 可删 | 并联 |
| :14855 | `_handle_post_dreaming` | `if not auth.is_admin:` | tenant_owner 可管本租户员工做梦 | 并联 |
| :15099 | `_check_agent_access` | `if not auth.is_admin and agent.get('createdBy') != auth.user_info['userId'] and agent.get('visibilit` | tenant_owner 可访问本租户员工 | 并联 |
| :17733 | `_handle_get_analyzed_talents` | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` | tenant_owner 看本租户分析(可见性) | 并联 |
| :18468 | `_handle_get_deals` | `if not auth.is_admin:` | tenant_owner 看本租户交易 | 并联 |
| :18512 | `_handle_get_deal_detail` | `if not auth.is_admin:` | tenant_owner 可读 | 并联 |
| :20217 | `_handle_get_products` | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` | tenant_owner 看本租户商品 | 并联 |
| :20408 | `_handle_get_tasks` | `if not auth.is_admin:` | tenant_owner 管本租户任务 | 并联 |
| :20434 | `_handle_get_task` | `if not auth.is_admin:` | tenant_owner 可读 | 并联 |
| :20447 | `_handle_post_task` | `if not auth.is_admin:` | tenant_owner 可派 | 并联 |
| :20509 | `_handle_put_task` | `if not auth.is_admin:` | tenant_owner 可改全部字段 | 并联(2处) |
| :20515 | `_handle_put_task` | `allowed_fields = ['status', 'progress'] if not auth.is_admin else ['title', 'description', 'assignee` | tenant_owner 可改全部字段 | 并联(2处) |
| :20540 | `_handle_delete_task` | `if not auth.is_admin:` | tenant_owner 可删 | 并联 |
| :21026 | `_handle_post_product` | `if not auth.is_admin:` | tenant_owner 可录 | 并联 |
| :21415 | `_handle_get_brands` | `if not auth.is_admin:` | tenant_owner 看本租户品牌 | 并联 |
| :21729 | `_handle_search_talents` | `is_admin = bool(auth.is_admin) and not getattr(auth, 'localhost_agent_id', None)` | tenant_owner 搜本租户达人 | 并联(2处) |
| :21730 | `_handle_search_talents` | `if not is_admin:` | tenant_owner 搜本租户达人 | 并联(2处) |
| :21799 | `_handle_get_talent` | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` | tenant_owner 可读 | 并联 |
| :22197 | `_handle_promote_talent` | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` | tenant_owner 可晋升本租户达人 | 并联 |
| :22990 | `_handle_get_influencers` | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` | tenant_owner 看本租户达人库 | 并联 |
| :30471 | `_talent_id_by_name` | `is_admin = bool(auth.is_admin) if auth else False` | tenant_owner 命中本租户(可见性) | 并联(3处) |
| :30476 | `_talent_id_by_name` | `if row and (is_admin or (row['created_by'] or '') == uid):` | tenant_owner 命中本租户(可见性) | 并联(3处) |
| :30483 | `_talent_id_by_name` | `if is_admin or (r['created_by'] or '') == uid:` | tenant_owner 命中本租户(可见性) | 并联(3处) |
| :30510 | `_extract_talent_from_text` | `is_admin = bool(auth.is_admin) if auth else False` | 同上 | 并联(3处) |
| :30523 | `_extract_talent_from_text` | `if is_admin or (row['created_by'] or '') == uid:` | 同上 | 并联(3处) |
| :30534 | `_extract_talent_from_text` | `if is_admin or (r['created_by'] or '') == uid:` | 同上 | 并联(3处) |
| :30557 | `_build_single_talent_injection` | `is_admin = bool(auth.is_admin) if auth else False` | 同上 | 并联(2处) |
| :30558 | `_build_single_talent_injection` | `if not is_admin and (t.get('created_by') or '') != uid:` | 同上 | 并联(2处) |
| :30668 | `_build_talent_injection_text` | `if auth.is_admin:` | 同上 | 并联(2处) |
| :30678 | `_build_talent_injection_text` | `if not auth.is_admin:` | 同上 | 并联(2处) |

## 3B. B · 知识库 scope 层 → ks 加租户维度（37 处）

| 行号 | 函数 | 现状代码 | 目标语义 | 改法 |
|---|---|---|---|---|
| :12469 | `_handle_global_search` | `is_admin=auth.is_admin,` | tenant_owner 可搜本租户 | 传参层 |
| :15278 | `_handle_get_memory` | `user_id=auth.user_id, is_admin=auth.is_admin,` | tenant_owner 可读本租户员工记忆 | 传参层 |
| :15961 | `_handle_induct_to_knowledge` | `if not auth.is_admin and auth.user_id != emp_id:` | tenant_owner 可收录本租户员工 | emp_id 校验保留 |
| :15987 | `_handle_archive_inducted` | `if not auth.is_admin and auth.user_id != emp_id:` | tenant_owner 可归档 | 同上 |
| :16221 | `_handle_trigger_summary` | `if not auth.is_admin and auth.user_id != emp_id:` | tenant_owner 可触发 | 同上 |
| :16301 | `_handle_post_summary_review` | `if not auth.is_admin and auth.user_id != row['emp_id']:` | tenant_owner 可审 | 同上 |
| :16349 | `_handle_post_agent_knowledge_base` | `if not auth.is_admin and auth.user_id != emp_id:` | tenant_owner 可配 | 同上 |
| :16447 | `_handle_get_knowledge` | `if auth.is_admin:` | tenant_owner 读本租户全部 scope | ks 传参加租户维度 |
| :16457 | `_handle_get_knowledge` | `is_admin=auth.is_admin, user_team_ids=auth.team_ids,` | tenant_owner 读本租户全部 scope | ks 传参加租户维度 |
| :16476 | `_handle_get_knowledge_detail` | `if not ks.can_read_knowledge(doc, auth.user_id, is_admin=auth.is_admin, user_team_ids=auth.team_ids,` | tenant_owner 可读 | 同上 |
| :16519 | `_handle_get_knowledge_search` | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` | tenant_owner 可检索本租户 | 同上 |
| :16561 | `_handle_post_knowledge` | `if not ks.can_create_knowledge(scope, auth.user_id, is_admin=auth.is_admin,` | tenant_owner 可建本租户 scope | 同上 |
| :16619 | `_handle_put_knowledge` | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` | tenant_owner 可改/移动 | 同上 |
| :16662 | `_handle_put_knowledge` | `if not ks.can_create_knowledge(target_scope, auth.user_id, is_admin=auth.is_admin,` | tenant_owner 可改/移动 | 同上 |
| :16703 | `_handle_delete_knowledge` | `if not ks.can_delete_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` | tenant_owner 可删本租户 | 同上 |
| :16709 | `_handle_delete_knowledge` | `deleted = ks.knowledge_delete(doc_id, is_admin=auth.is_admin)` | tenant_owner 可删本租户 | 同上 |
| :16769 | `_handle_knowledge_rollback` | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` | tenant_owner 可回滚 | 同上 |
| :16826 | `_handle_knowledge_move` | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` | tenant_owner 可移动 | 同上 |
| :16844 | `_handle_knowledge_move` | `if not ks.can_create_knowledge(new_scope, auth.user_id, is_admin=auth.is_admin,` | tenant_owner 可移动 | 同上 |
| :16912 | `_handle_get_kb_entries` | `if auth.is_admin:` | tenant_owner 读本租户全部 | 同上 |
| :16924 | `_handle_get_kb_entries` | `is_admin=auth.is_admin, user_team_ids=auth.team_ids,` | tenant_owner 读本租户全部 | 同上 |
| :16947 | `_handle_get_kb_entry_detail` | `if not ks.can_read_knowledge(doc, auth.user_id, is_admin=auth.is_admin, user_team_ids=auth.team_ids,` | tenant_owner 可读 | 同上 |
| :16987 | `_handle_post_kb_entry` | `if not ks.can_create_knowledge(scope, auth.user_id, is_admin=auth.is_admin,` | tenant_owner 可建 | 同上 |
| :17236 | `_handle_put_kb_entry` | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` | tenant_owner 可改 | 同上 |
| :17262 | `_handle_put_kb_entry` | `if not ks.can_create_knowledge(target_scope, auth.user_id, is_admin=auth.is_admin,` | tenant_owner 可改 | 同上 |
| :17297 | `_handle_put_kb_entry` | `is_admin=auth.is_admin,` | tenant_owner 可改 | 同上 |
| :17405 | `_handle_post_kb_conflict_decide` | `doc, auth.user_id, is_admin=auth.is_admin,` | tenant_owner 可裁决本租户簇 | 同上 |
| :17412 | `_handle_post_kb_conflict_decide` | `ks.kb_entry_delete(eid, is_admin=auth.is_admin,` | tenant_owner 可裁决本租户簇 | 同上 |
| :17447 | `_handle_delete_kb_entry` | `if not ks.can_delete_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` | tenant_owner 可删 | 同上 |
| :17455 | `_handle_delete_kb_entry` | `deleted = ks.kb_entry_delete(entry_id, is_admin=auth.is_admin, operator_id=auth.user_id)` | tenant_owner 可删 | 同上 |
| :17665 | `_handle_get_kb_stats` | `is_admin=auth.is_admin, user_team_ids=auth.team_ids,` | tenant_owner 看本租户统计 | 同上 |
| :18676 | `_handle_post_kb_search` | `user_id=auth.user_id, is_admin=auth.is_admin,` | tenant_owner 可检索 | 同上 |
| :19717 | `_handle_post_rag_retrieve` | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` | tenant_owner 检索范围=本租户 | 同上 |
| :24719 | `_handle_post_chat_impl` | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` | tenant_owner 注入范围=本租户 | 同上(3处) |
| :24757 | `_handle_post_chat_impl` | `requester_id=auth.user_id, is_admin=auth.is_admin,` | tenant_owner 注入范围=本租户 | 同上(3处) |
| :24811 | `_handle_post_chat_impl` | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` | tenant_owner 注入范围=本租户 | 同上(3处) |
| :32841 | `_wrap_reply` | `requester_id=requester_id, is_admin=is_admin, team_ids=team_ids,` | 透传层 | 跟随 chat 链路 |

## 3C. C · 平台维护面 → platform only（12 处）

| 行号 | 函数 | 现状代码 | 目标语义 | 改法 |
|---|---|---|---|---|
| :17034 | `_handle_post_kb_reindex` | `if not auth.is_admin:` | platform only | 批0切 is_platform_admin |
| :17176 | `_handle_post_kb_retry_embedding` | `is_admin=auth.is_admin,` | platform only（单条重试） | 可议：tenant_owner 对本租户 entry |
| :17205 | `_handle_post_kb_retry_all_failed` | `if not auth.is_admin:` | platform only | 批0 |
| :17210 | `_handle_post_kb_retry_all_failed` | `is_admin=True, operator_id=auth.user_id,` | platform only | 批0 |
| :17488 | `_handle_cleanup_kb_dangling` | `if not auth.is_admin:` | platform only | 批0(2处) |
| :17502 | `_handle_cleanup_kb_dangling` | `stats = ks.kb_entry_cleanup_dangling(days_old=days_old, is_admin=True)` | platform only | 批0(2处) |
| :17519 | `_handle_post_verify_kb_index` | `if not auth.is_admin:` | platform only | 批0(2处) |
| :17534 | `_handle_post_verify_kb_index` | `result = ks.kb_entry_verify_index(entry_id=entry_id, limit_per_type=limit, is_admin=True)` | platform only | 批0(2处) |
| :17553 | `_handle_post_repair_kb_index` | `if not auth.is_admin:` | platform only | 批0(2处) |
| :17561 | `_handle_post_repair_kb_index` | `result = ks.kb_entry_repair_index(entry_id=entry_id, confirm=confirm, is_admin=True)` | platform only | 批0(2处) |
| :18064 | `_handle_get_knowledge_patterns` | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` | platform only | 规律库是平台资产 |
| :18699 | `_handle_get_stats_compute` | `if not auth.is_admin:` | platform only | 用量统计运营面 |

## 3D. D · 框架/锚点（不动）（2 处）

| 行号 | 函数 | 现状代码 | 目标语义 | 改法 |
|---|---|---|---|---|
| :2661 | `is_admin` | `def is_admin(self):` | 语义锚点保留 | AuthResult.is_admin property 本体 |
| :32697 | `_call_ai_api` | `allowed_knowledge_categories=None, requester_id=None, is_admin=False, team_ids=None,` | 签名默认参数 | is_admin=False 默认形参 |

## 4. 决策点（2026-11-09 老大拍板 ✅）

1. **tenant_owner 的"本租户"边界**：先身份后隔离 ✅ 拍板。阶段 2 只做角色，行级 tenant_id 隔离归阶段 3。
2. **B 桶知识库口径** ✅ 拍板：**合伙人穿透看本租户全部**（= 平台超管现在的视角，限本租户；员工间 personal 仍互相隔离，唯一例外是合伙人）。改/删同权，留痕审计。
3. **C 桶 `_handle_post_kb_retry_embedding`（单条重试）** ✅ 拍板：**平台维护全部归 platform only**（含单条重试；批量维护更不例外）。
4. **D 桶 `AuthResult.is_admin` property 的最终去留** ✅ 按建议执行（老大无异议）：保留 property 当权限菜单计算零件，业务零消费，打标清单归零即验收，不物理删除。
5. **种子数据**：⏳ 待拍板——第一批 tenant_owner 是谁（批 1 开工时由 super_admin 指定，grant 口子随批 1 一起上）。

## 5. 分批执行计划（每批：切 → 守卫测试 → 全量回归 → 推送 → 重启，同阶段 1 纪律）

| 批次 | 内容 | 处数 | 风险 |
|---|---|---|---|
| 阶段2批0 | C 桶平台维护面切 `is_platform_admin` | 12 | 低，纯收窄 |
| 阶段2批1 | A 桶业务管理面并联 `is_platform_admin or is_tenant_owner` + tenant_owner property + grant 口子 + 种子 | 40 | 中，含新 property 与开角色 |
| 阶段2批2 | B 桶 ks 传参层加租户维度 | 37 | 高，动 ks.can_*/rag 核心，需 ks 层单测先补齐 |
| 阶段2批3 | D 桶验收：打标清单 [tenant] 归零、is_admin 零业务消费 | — | 回归 |

## 6. 与既有文档的关系

- 本文档是 `docs/IS-ADMIN-LABELS.md`（阶段 1）的下半场：`[platform]` 已清零（41 处切完 + 2 处硬编码名单清除），`[tenant]` 91 处的去向以本文档为准。
- kind 枚举扩展：`platform_internal`（阶段 1 收口新增）之外，阶段 2 批 1 再加 `tenant_owner`，DDL 注释同步。
