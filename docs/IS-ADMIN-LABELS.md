# is_admin 逐处打标清单（阶段 1 前置 #1）

> 生成：`python3 scripts/label_is_admin_sites.py > docs/IS-ADMIN-LABELS.md`
> 口径：docs/SUBJECTS-PACKS-RECONCILE.md §2#1 / §5。改代码后行号会漂，重跑本脚本。
> 本次扫描：**87 处消费** = [platform] 3 + [tenant] 84；另有 framework 2、definition 1、comment 5。

**拆主开关顺序（§2#1 红线）：先按本清单把 [platform] 消费切到 super_admin，[tenant] 消费保留 is_admin 语义（后续归 tenant_owner），每切一批跑全量回归。**

| 行号 | 所在函数 | 标签 | 代码 | 备注 |
|---|---|---|---|---|
| :1875 | `_get_effective_permissions` | **[framework]** | `if hasattr(user_or_auth, 'is_admin') and (user_or_auth.is_admin` | 权限计算核心：hasattr 探测 is_admin，是权限管理的输入 |
| :2661 | `is_admin` | **[tenant]** | `def is_admin(self):` |  |
| :2873 | `_check_talent_write_permission` | **[tenant]** | `if auth.is_admin and not getattr(auth, 'localhost_agent_id', None):` |  |
| :3001 | `_api_token_auth_result` | **[comment]** | `★ scoped token 永远 role='user'：即使绑定 admin 用户也不放行 is_admin 检查，` | 注释/docstring 字样，无权限语义 |
| :3186 | `_get_accessible_agent_ids` | **[tenant]** | `if auth.is_admin:` |  |
| :3258 | `_require_admin` | **[framework]** | `语义对照：切换前 is_platform_admin ≡ is_admin（同读 role=='admin'），` | 通用闸门 helper 本体，消费方集中在权限/租户管理 |
| :12469 | `_handle_global_search` | **[tenant]** | `is_admin=auth.is_admin,` |  |
| :13492 | `_handle_get_agents` | **[tenant]** | `if auth.is_admin:` |  |
| :13572 | `_handle_get_agent` | **[tenant]** | `if not auth.is_admin:` |  |
| :13627 | `_handle_get_agent_stats` | **[tenant]** | `if not auth.is_admin:` |  |
| :13633 | `_handle_get_agent_stats` | **[tenant]** | `is_scope_filtered = not (auth.is_admin and not getattr(auth, 'localhost_agent_id', None))` |  |
| :13755 | `_handle_get_agent_activity` | **[tenant]** | `if not auth.is_admin:` |  |
| :13760 | `_handle_get_agent_activity` | **[tenant]** | `is_scope_filtered = not (auth.is_admin and not getattr(auth, 'localhost_agent_id', None))` |  |
| :14029 | `_handle_create_agent_inner` | **[tenant]** | `if not auth.is_admin:` |  |
| :14170 | `_handle_update_agent` | **[tenant]** | `if not auth.is_admin:` |  |
| :14372 | `_handle_delete_agent` | **[tenant]** | `if not auth.is_admin:` |  |
| :14855 | `_handle_post_dreaming` | **[tenant]** | `if not auth.is_admin:` |  |
| :15099 | `_check_agent_access` | **[tenant]** | `if not auth.is_admin and agent.get('createdBy') != auth.user_info['userId'] and agent.get('visibilit` |  |
| :15278 | `_handle_get_memory` | **[tenant]** | `user_id=auth.user_id, is_admin=auth.is_admin,` |  |
| :15961 | `_handle_induct_to_knowledge` | **[tenant]** | `if not auth.is_admin and auth.user_id != emp_id:` |  |
| :15987 | `_handle_archive_inducted` | **[tenant]** | `if not auth.is_admin and auth.user_id != emp_id:` |  |
| :16221 | `_handle_trigger_summary` | **[tenant]** | `if not auth.is_admin and auth.user_id != emp_id:` |  |
| :16301 | `_handle_post_summary_review` | **[tenant]** | `if not auth.is_admin and auth.user_id != row['emp_id']:` |  |
| :16349 | `_handle_post_agent_knowledge_base` | **[tenant]** | `if not auth.is_admin and auth.user_id != emp_id:` |  |
| :16447 | `_handle_get_knowledge` | **[tenant]** | `if auth.is_admin:` |  |
| :16457 | `_handle_get_knowledge` | **[tenant]** | `is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :16476 | `_handle_get_knowledge_detail` | **[tenant]** | `if not ks.can_read_knowledge(doc, auth.user_id, is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :16519 | `_handle_get_knowledge_search` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` |  |
| :16561 | `_handle_post_knowledge` | **[tenant]** | `if not ks.can_create_knowledge(scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :16619 | `_handle_put_knowledge` | **[tenant]** | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :16662 | `_handle_put_knowledge` | **[tenant]** | `if not ks.can_create_knowledge(target_scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :16703 | `_handle_delete_knowledge` | **[tenant]** | `if not ks.can_delete_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :16709 | `_handle_delete_knowledge` | **[tenant]** | `deleted = ks.knowledge_delete(doc_id, is_admin=auth.is_admin)` |  |
| :16769 | `_handle_knowledge_rollback` | **[tenant]** | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :16826 | `_handle_knowledge_move` | **[tenant]** | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :16844 | `_handle_knowledge_move` | **[tenant]** | `if not ks.can_create_knowledge(new_scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :16912 | `_handle_get_kb_entries` | **[tenant]** | `if auth.is_admin:` |  |
| :16924 | `_handle_get_kb_entries` | **[tenant]** | `is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :16947 | `_handle_get_kb_entry_detail` | **[tenant]** | `if not ks.can_read_knowledge(doc, auth.user_id, is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :16987 | `_handle_post_kb_entry` | **[tenant]** | `if not ks.can_create_knowledge(scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :17177 | `_handle_post_kb_retry_embedding` | **[tenant]** | `is_admin=auth.is_platform_admin,` |  |
| :17212 | `_handle_post_kb_retry_all_failed` | **[tenant]** | `is_admin=True, operator_id=auth.user_id,` |  |
| :17238 | `_handle_put_kb_entry` | **[tenant]** | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :17264 | `_handle_put_kb_entry` | **[tenant]** | `if not ks.can_create_knowledge(target_scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :17299 | `_handle_put_kb_entry` | **[tenant]** | `is_admin=auth.is_admin,` |  |
| :17407 | `_handle_post_kb_conflict_decide` | **[tenant]** | `doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :17414 | `_handle_post_kb_conflict_decide` | **[tenant]** | `ks.kb_entry_delete(eid, is_admin=auth.is_admin,` |  |
| :17449 | `_handle_delete_kb_entry` | **[tenant]** | `if not ks.can_delete_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :17457 | `_handle_delete_kb_entry` | **[tenant]** | `deleted = ks.kb_entry_delete(entry_id, is_admin=auth.is_admin, operator_id=auth.user_id)` |  |
| :17505 | `_handle_cleanup_kb_dangling` | **[tenant]** | `stats = ks.kb_entry_cleanup_dangling(days_old=days_old, is_admin=True)` |  |
| :17538 | `_handle_post_verify_kb_index` | **[tenant]** | `result = ks.kb_entry_verify_index(entry_id=entry_id, limit_per_type=limit, is_admin=True)` |  |
| :17566 | `_handle_post_repair_kb_index` | **[tenant]** | `result = ks.kb_entry_repair_index(entry_id=entry_id, confirm=confirm, is_admin=True)` |  |
| :17670 | `_handle_get_kb_stats` | **[tenant]** | `is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :17691 | `_handle_get_llm_usage` | **[comment]** | `# （scoped token 已先过 _required_api_scope 闸门；其 role 恒为 user，不认 is_admin）` | 注释/docstring 字样，无权限语义 |
| :17738 | `_handle_get_analyzed_talents` | **[tenant]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :18473 | `_handle_get_deals` | **[tenant]** | `if not auth.is_admin:` |  |
| :18517 | `_handle_get_deal_detail` | **[tenant]** | `if not auth.is_admin:` |  |
| :18681 | `_handle_post_kb_search` | **[tenant]** | `user_id=auth.user_id, is_admin=auth.is_admin,` |  |
| :19145 | `_handle_get_feishu_role_content` | **[platform]** | `if not (auth.is_admin or auth.is_tenant_admin):` |  |
| :19722 | `_handle_post_rag_retrieve` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` |  |
| :20222 | `_handle_get_products` | **[tenant]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :20413 | `_handle_get_tasks` | **[tenant]** | `if not auth.is_admin:` |  |
| :20439 | `_handle_get_task` | **[tenant]** | `if not auth.is_admin:` |  |
| :20452 | `_handle_post_task` | **[tenant]** | `if not auth.is_admin:` |  |
| :20514 | `_handle_put_task` | **[tenant]** | `if not auth.is_admin:` |  |
| :20520 | `_handle_put_task` | **[tenant]** | `allowed_fields = ['status', 'progress'] if not auth.is_admin else ['title', 'description', 'assignee` |  |
| :20545 | `_handle_delete_task` | **[tenant]** | `if not auth.is_admin:` |  |
| :21031 | `_handle_post_product` | **[tenant]** | `if not auth.is_admin:` |  |
| :21420 | `_handle_get_brands` | **[tenant]** | `if not auth.is_admin:` |  |
| :21734 | `_handle_search_talents` | **[tenant]** | `is_admin = bool(auth.is_admin) and not getattr(auth, 'localhost_agent_id', None)` |  |
| :21735 | `_handle_search_talents` | **[tenant]** | `if not is_admin:` |  |
| :21804 | `_handle_get_talent` | **[tenant]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :22202 | `_handle_promote_talent` | **[tenant]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :22743 | `_handle_get_proposals` | **[comment]** | `# ★ GET admin-only 收窄 (跟 resolve 硬闸 is_admin 一致, 老大 19:05 拍板)` | 注释/docstring 字样，无权限语义 |
| :22947 | `_handle_delete_proposal` | **[platform]** | `is_admin = auth.is_platform_admin and not getattr(auth, 'localhost_agent_id', None)` |  |
| :22948 | `_handle_delete_proposal` | **[platform]** | `if not (is_creator or is_admin):` |  |
| :22995 | `_handle_get_influencers` | **[tenant]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :24724 | `_handle_post_chat_impl` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` |  |
| :24762 | `_handle_post_chat_impl` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin,` |  |
| :24816 | `_handle_post_chat_impl` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` |  |
| :30476 | `_talent_id_by_name` | **[tenant]** | `is_admin = bool(auth.is_admin) if auth else False` |  |
| :30481 | `_talent_id_by_name` | **[tenant]** | `if row and (is_admin or (row['created_by'] or '') == uid):` |  |
| :30488 | `_talent_id_by_name` | **[tenant]** | `if is_admin or (r['created_by'] or '') == uid:` |  |
| :30515 | `_extract_talent_from_text` | **[tenant]** | `is_admin = bool(auth.is_admin) if auth else False` |  |
| :30528 | `_extract_talent_from_text` | **[tenant]** | `if is_admin or (row['created_by'] or '') == uid:` |  |
| :30539 | `_extract_talent_from_text` | **[tenant]** | `if is_admin or (r['created_by'] or '') == uid:` |  |
| :30562 | `_build_single_talent_injection` | **[tenant]** | `is_admin = bool(auth.is_admin) if auth else False` |  |
| :30563 | `_build_single_talent_injection` | **[tenant]** | `if not is_admin and (t.get('created_by') or '') != uid:` |  |
| :30661 | `_build_talent_injection_text` | **[comment]** | `数据隔离规则：管理员（is_admin）看全部，非管理员只看自己（created_by=user_id）录入的。` | 注释/docstring 字样，无权限语义 |
| :30673 | `_build_talent_injection_text` | **[tenant]** | `if auth.is_admin:` |  |
| :30683 | `_build_talent_injection_text` | **[tenant]** | `if not auth.is_admin:` |  |
| :32702 | `_call_ai_api` | **[tenant]** | `allowed_knowledge_categories=None, requester_id=None, is_admin=False, team_ids=None,` |  |
| :32841 | `_wrap_reply` | **[comment]** | `#   入参与返回结构与 rag_retrieve 完全一致；四层 scope 隔离由 requester_id/is_admin/team_ids/group_ids 承载。` | 注释/docstring 字样，无权限语义 |
| :32846 | `_wrap_reply` | **[tenant]** | `requester_id=requester_id, is_admin=is_admin, team_ids=team_ids,` |  |

## 按域汇总

### [platform] 3 处 / 2 个函数

- `_handle_delete_proposal` × 2
- `_handle_get_feishu_role_content` × 1

### [tenant] 84 处 / 63 个函数

- `_handle_put_kb_entry` × 3
- `_handle_post_chat_impl` × 3
- `_talent_id_by_name` × 3
- `_extract_talent_from_text` × 3
- `_handle_get_agent_stats` × 2
- `_handle_get_agent_activity` × 2
- `_handle_get_knowledge` × 2
- `_handle_put_knowledge` × 2
- `_handle_delete_knowledge` × 2
- `_handle_knowledge_move` × 2
- `_handle_get_kb_entries` × 2
- `_handle_post_kb_conflict_decide` × 2
- `_handle_delete_kb_entry` × 2
- `_handle_put_task` × 2
- `_handle_search_talents` × 2
- `_build_single_talent_injection` × 2
- `_build_talent_injection_text` × 2
- `is_admin` × 1
- `_check_talent_write_permission` × 1
- `_get_accessible_agent_ids` × 1
- `_handle_global_search` × 1
- `_handle_get_agents` × 1
- `_handle_get_agent` × 1
- `_handle_create_agent_inner` × 1
- `_handle_update_agent` × 1
- `_handle_delete_agent` × 1
- `_handle_post_dreaming` × 1
- `_check_agent_access` × 1
- `_handle_get_memory` × 1
- `_handle_induct_to_knowledge` × 1
- `_handle_archive_inducted` × 1
- `_handle_trigger_summary` × 1
- `_handle_post_summary_review` × 1
- `_handle_post_agent_knowledge_base` × 1
- `_handle_get_knowledge_detail` × 1
- `_handle_get_knowledge_search` × 1
- `_handle_post_knowledge` × 1
- `_handle_knowledge_rollback` × 1
- `_handle_get_kb_entry_detail` × 1
- `_handle_post_kb_entry` × 1
- `_handle_post_kb_retry_embedding` × 1
- `_handle_post_kb_retry_all_failed` × 1
- `_handle_cleanup_kb_dangling` × 1
- `_handle_post_verify_kb_index` × 1
- `_handle_post_repair_kb_index` × 1
- `_handle_get_kb_stats` × 1
- `_handle_get_analyzed_talents` × 1
- `_handle_get_deals` × 1
- `_handle_get_deal_detail` × 1
- `_handle_post_kb_search` × 1
- `_handle_post_rag_retrieve` × 1
- `_handle_get_products` × 1
- `_handle_get_tasks` × 1
- `_handle_get_task` × 1
- `_handle_post_task` × 1
- `_handle_delete_task` × 1
- `_handle_post_product` × 1
- `_handle_get_brands` × 1
- `_handle_get_talent` × 1
- `_handle_promote_talent` × 1
- `_handle_get_influencers` × 1
- `_call_ai_api` × 1
- `_wrap_reply` × 1

