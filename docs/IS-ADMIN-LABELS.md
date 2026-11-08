# is_admin 逐处打标清单（阶段 1 前置 #1）

> 生成：`python3 scripts/label_is_admin_sites.py > docs/IS-ADMIN-LABELS.md`
> 口径：docs/SUBJECTS-PACKS-RECONCILE.md §2#1 / §5。改代码后行号会漂，重跑本脚本。
> 本次扫描：**131 处消费** = [platform] 41 + [tenant] 90；另有 framework 2、definition 1、comment 5。

**拆主开关顺序（§2#1 红线）：先按本清单把 [platform] 消费切到 super_admin，[tenant] 消费保留 is_admin 语义（后续归 tenant_owner），每切一批跑全量回归。**

| 行号 | 所在函数 | 标签 | 代码 | 备注 |
|---|---|---|---|---|
| :1875 | `_get_effective_permissions` | **[framework]** | `if hasattr(user_or_auth, 'is_admin') and (user_or_auth.is_admin` | 权限计算核心：hasattr 探测 is_admin，是权限管理的输入 |
| :2626 | `is_admin` | **[definition]** | `def is_admin(self):` | is_admin property 本体（主开关定义，非消费） |
| :2823 | `_check_talent_write_permission` | **[tenant]** | `if auth.is_admin and not getattr(auth, 'localhost_agent_id', None):` |  |
| :2951 | `_api_token_auth_result` | **[comment]** | `★ scoped token 永远 role='user'：即使绑定 admin 用户也不放行 is_admin 检查，` | 注释/docstring 字样，无权限语义 |
| :3109 | `_can_access_team` | **[platform]** | `if auth.is_admin:` |  |
| :3136 | `_get_accessible_agent_ids` | **[tenant]** | `if auth.is_admin:` |  |
| :3207 | `_require_admin` | **[framework]** | `if not auth.is_admin:` | 通用闸门 helper 本体，消费方集中在权限/租户管理 |
| :10957 | `_handle_get_permissions` | **[platform]** | `if not auth.is_authenticated or not auth.is_admin:` |  |
| :11031 | `_handle_update_role_permissions` | **[platform]** | `if not auth.is_authenticated or not auth.is_admin:` |  |
| :11061 | `_handle_update_user_permissions` | **[platform]** | `if not auth.is_authenticated or not auth.is_admin:` |  |
| :11904 | `_handle_get_user_subordinates` | **[platform]** | `if not auth.is_admin:` |  |
| :12013 | `_check_group_access` | **[platform]** | `if auth.is_admin or group.get('createdBy') == auth.user_info.get('userId'):` |  |
| :12044 | `_handle_get_groups` | **[platform]** | `if not auth.is_admin:` |  |
| :12222 | `_handle_update_group` | **[platform]** | `if not auth.is_admin and group.get('createdBy') != auth.user_info['userId']:` |  |
| :12283 | `_handle_batch_save_groups` | **[platform]** | `if auth.is_admin:` |  |
| :12310 | `_handle_delete_group` | **[platform]** | `if not auth.is_admin and group.get('createdBy') != auth.user_info['userId']:` |  |
| :12413 | `_handle_global_search` | **[tenant]** | `is_admin=auth.is_admin,` |  |
| :12450 | `_handle_add_group_member` | **[platform]** | `if not auth.is_admin and group.get('createdBy') != auth.user_info['userId']:` |  |
| :12504 | `_handle_remove_group_member` | **[platform]** | `if not auth.is_admin and group.get('createdBy') != auth.user_info['userId']:` |  |
| :12594 | `_handle_get_team` | **[platform]** | `if not auth.is_admin:` |  |
| :12740 | `_handle_update_team` | **[platform]** | `if not auth.is_admin:` |  |
| :12798 | `_handle_delete_team` | **[platform]** | `if not auth.is_admin:` |  |
| :12845 | `_handle_add_team_member` | **[platform]** | `if not auth.is_admin:` |  |
| :12890 | `_handle_remove_team_member` | **[platform]** | `if not auth.is_admin:` |  |
| :13436 | `_handle_get_agents` | **[tenant]** | `if auth.is_admin:` |  |
| :13516 | `_handle_get_agent` | **[tenant]** | `if not auth.is_admin:` |  |
| :13571 | `_handle_get_agent_stats` | **[tenant]** | `if not auth.is_admin:` |  |
| :13577 | `_handle_get_agent_stats` | **[tenant]** | `is_scope_filtered = not (auth.is_admin and not getattr(auth, 'localhost_agent_id', None))` |  |
| :13699 | `_handle_get_agent_activity` | **[tenant]** | `if not auth.is_admin:` |  |
| :13704 | `_handle_get_agent_activity` | **[tenant]** | `is_scope_filtered = not (auth.is_admin and not getattr(auth, 'localhost_agent_id', None))` |  |
| :13973 | `_handle_create_agent_inner` | **[tenant]** | `if not auth.is_admin:` |  |
| :14114 | `_handle_update_agent` | **[tenant]** | `if not auth.is_admin:` |  |
| :14316 | `_handle_delete_agent` | **[tenant]** | `if not auth.is_admin:` |  |
| :14799 | `_handle_post_dreaming` | **[tenant]** | `if not auth.is_admin:` |  |
| :15043 | `_check_agent_access` | **[tenant]** | `if not auth.is_admin and agent.get('createdBy') != auth.user_info['userId'] and agent.get('visibilit` |  |
| :15222 | `_handle_get_memory` | **[tenant]** | `user_id=auth.user_id, is_admin=auth.is_admin,` |  |
| :15905 | `_handle_induct_to_knowledge` | **[tenant]** | `if not auth.is_admin and auth.user_id != emp_id:` |  |
| :15931 | `_handle_archive_inducted` | **[tenant]** | `if not auth.is_admin and auth.user_id != emp_id:` |  |
| :16165 | `_handle_trigger_summary` | **[tenant]** | `if not auth.is_admin and auth.user_id != emp_id:` |  |
| :16245 | `_handle_post_summary_review` | **[tenant]** | `if not auth.is_admin and auth.user_id != row['emp_id']:` |  |
| :16293 | `_handle_post_agent_knowledge_base` | **[tenant]** | `if not auth.is_admin and auth.user_id != emp_id:` |  |
| :16391 | `_handle_get_knowledge` | **[tenant]** | `if auth.is_admin:` |  |
| :16401 | `_handle_get_knowledge` | **[tenant]** | `is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :16420 | `_handle_get_knowledge_detail` | **[tenant]** | `if not ks.can_read_knowledge(doc, auth.user_id, is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :16463 | `_handle_get_knowledge_search` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` |  |
| :16505 | `_handle_post_knowledge` | **[tenant]** | `if not ks.can_create_knowledge(scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :16563 | `_handle_put_knowledge` | **[tenant]** | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :16606 | `_handle_put_knowledge` | **[tenant]** | `if not ks.can_create_knowledge(target_scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :16647 | `_handle_delete_knowledge` | **[tenant]** | `if not ks.can_delete_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :16653 | `_handle_delete_knowledge` | **[tenant]** | `deleted = ks.knowledge_delete(doc_id, is_admin=auth.is_admin)` |  |
| :16713 | `_handle_knowledge_rollback` | **[tenant]** | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :16770 | `_handle_knowledge_move` | **[tenant]** | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :16788 | `_handle_knowledge_move` | **[tenant]** | `if not ks.can_create_knowledge(new_scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :16856 | `_handle_get_kb_entries` | **[tenant]** | `if auth.is_admin:` |  |
| :16868 | `_handle_get_kb_entries` | **[tenant]** | `is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :16891 | `_handle_get_kb_entry_detail` | **[tenant]** | `if not ks.can_read_knowledge(doc, auth.user_id, is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :16931 | `_handle_post_kb_entry` | **[tenant]** | `if not ks.can_create_knowledge(scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :16978 | `_handle_post_kb_reindex` | **[tenant]** | `if not auth.is_admin:` |  |
| :17021 | `_handle_post_pipe_start` | **[platform]** | `if not auth.is_admin:` |  |
| :17054 | `_handle_get_pipe_progress` | **[platform]** | `if not auth.is_admin:` |  |
| :17073 | `_handle_post_pipe_cancel` | **[platform]** | `if not auth.is_admin:` |  |
| :17092 | `_handle_get_pipe_list` | **[platform]** | `if not auth.is_admin:` |  |
| :17120 | `_handle_post_kb_retry_embedding` | **[tenant]** | `is_admin=auth.is_admin,` |  |
| :17149 | `_handle_post_kb_retry_all_failed` | **[tenant]** | `if not auth.is_admin:` |  |
| :17154 | `_handle_post_kb_retry_all_failed` | **[tenant]** | `is_admin=True, operator_id=auth.user_id,` |  |
| :17180 | `_handle_put_kb_entry` | **[tenant]** | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :17206 | `_handle_put_kb_entry` | **[tenant]** | `if not ks.can_create_knowledge(target_scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :17241 | `_handle_put_kb_entry` | **[tenant]** | `is_admin=auth.is_admin,` |  |
| :17349 | `_handle_post_kb_conflict_decide` | **[tenant]** | `doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :17356 | `_handle_post_kb_conflict_decide` | **[tenant]** | `ks.kb_entry_delete(eid, is_admin=auth.is_admin,` |  |
| :17391 | `_handle_delete_kb_entry` | **[tenant]** | `if not ks.can_delete_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :17399 | `_handle_delete_kb_entry` | **[tenant]** | `deleted = ks.kb_entry_delete(entry_id, is_admin=auth.is_admin, operator_id=auth.user_id)` |  |
| :17432 | `_handle_cleanup_kb_dangling` | **[tenant]** | `if not auth.is_admin:` |  |
| :17446 | `_handle_cleanup_kb_dangling` | **[tenant]** | `stats = ks.kb_entry_cleanup_dangling(days_old=days_old, is_admin=True)` |  |
| :17463 | `_handle_post_verify_kb_index` | **[tenant]** | `if not auth.is_admin:` |  |
| :17478 | `_handle_post_verify_kb_index` | **[tenant]** | `result = ks.kb_entry_verify_index(entry_id=entry_id, limit_per_type=limit, is_admin=True)` |  |
| :17497 | `_handle_post_repair_kb_index` | **[tenant]** | `if not auth.is_admin:` |  |
| :17505 | `_handle_post_repair_kb_index` | **[tenant]** | `result = ks.kb_entry_repair_index(entry_id=entry_id, confirm=confirm, is_admin=True)` |  |
| :17609 | `_handle_get_kb_stats` | **[tenant]** | `is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :17630 | `_handle_get_llm_usage` | **[comment]** | `# （scoped token 已先过 _required_api_scope 闸门；其 role 恒为 user，不认 is_admin）` | 注释/docstring 字样，无权限语义 |
| :17631 | `_handle_get_llm_usage` | **[platform]** | `if not auth.is_admin and 'admin' not in (getattr(auth, 'api_scopes', None) or ()):` |  |
| :17677 | `_handle_get_analyzed_talents` | **[tenant]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :18008 | `_handle_get_knowledge_patterns` | **[tenant]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :18412 | `_handle_get_deals` | **[tenant]** | `if not auth.is_admin:` |  |
| :18456 | `_handle_get_deal_detail` | **[tenant]** | `if not auth.is_admin:` |  |
| :18620 | `_handle_post_kb_search` | **[tenant]** | `user_id=auth.user_id, is_admin=auth.is_admin,` |  |
| :18643 | `_handle_get_stats_compute` | **[tenant]** | `if not auth.is_admin:` |  |
| :18799 | `_handle_credits_admin_grant` | **[platform]** | `if not (auth.is_admin or auth.is_tenant_admin):  # ★ M5: 租户管理员可管内务` |  |
| :18915 | `_handle_credit_recharge` | **[platform]** | `if not (auth.is_admin or auth.is_tenant_admin):  # ★ M5: 租户管理员可管内务` |  |
| :18952 | `_handle_credit_recharge_generic` | **[platform]** | `if not (auth.is_admin or auth.is_tenant_admin):  # ★ M5: 租户管理员可管内务` |  |
| :18987 | `_handle_get_feishu_credits` | **[platform]** | `if not (auth.is_admin or auth.is_tenant_admin):` |  |
| :19004 | `_handle_post_feishu_credits_recharge` | **[platform]** | `if not (auth.is_admin or auth.is_tenant_admin):` |  |
| :19034 | `_handle_get_feishu_console` | **[platform]** | `if not (auth.is_admin or auth.is_tenant_admin):` |  |
| :19063 | `_handle_post_feishu_console_agent_op` | **[platform]** | `if not (auth.is_admin or auth.is_tenant_admin):` |  |
| :19079 | `_handle_get_feishu_role_content` | **[platform]** | `if not (auth.is_admin or auth.is_tenant_admin):` |  |
| :19110 | `_handle_get_feishu_chat` | **[platform]** | `if not (auth.is_admin or auth.is_tenant_admin):` |  |
| :19133 | `_handle_get_feishu_facts` | **[platform]** | `if not (auth.is_admin or auth.is_tenant_admin):` |  |
| :19152 | `_handle_post_feishu_fact_delete` | **[platform]** | `if not (auth.is_admin or auth.is_tenant_admin):` |  |
| :19170 | `_handle_post_feishu_tenant_create` | **[platform]** | `if not (auth.is_admin or auth.is_tenant_admin):` |  |
| :19470 | `_handle_get_token_usage` | **[platform]** | `if not auth.is_admin:` |  |
| :19655 | `_handle_post_rag_retrieve` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` |  |
| :20155 | `_handle_get_products` | **[tenant]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :20346 | `_handle_get_tasks` | **[tenant]** | `if not auth.is_admin:` |  |
| :20372 | `_handle_get_task` | **[tenant]** | `if not auth.is_admin:` |  |
| :20385 | `_handle_post_task` | **[tenant]** | `if not auth.is_admin:` |  |
| :20447 | `_handle_put_task` | **[tenant]** | `if not auth.is_admin:` |  |
| :20453 | `_handle_put_task` | **[tenant]** | `allowed_fields = ['status', 'progress'] if not auth.is_admin else ['title', 'description', 'assignee` |  |
| :20478 | `_handle_delete_task` | **[tenant]** | `if not auth.is_admin:` |  |
| :20964 | `_handle_post_product` | **[tenant]** | `if not auth.is_admin:` |  |
| :21353 | `_handle_get_brands` | **[tenant]** | `if not auth.is_admin:` |  |
| :21667 | `_handle_search_talents` | **[tenant]** | `is_admin = bool(auth.is_admin) and not getattr(auth, 'localhost_agent_id', None)` |  |
| :21668 | `_handle_search_talents` | **[tenant]** | `if not is_admin:` |  |
| :21737 | `_handle_get_talent` | **[tenant]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :22135 | `_handle_promote_talent` | **[tenant]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :22634 | `_handle_get_agent_calls` | **[platform]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :22676 | `_handle_get_proposals` | **[comment]** | `# ★ GET admin-only 收窄 (跟 resolve 硬闸 is_admin 一致, 老大 19:05 拍板)` | 注释/docstring 字样，无权限语义 |
| :22678 | `_handle_get_proposals` | **[platform]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :22716 | `_handle_get_proposal` | **[platform]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :22745 | `_handle_post_proposal_resolve` | **[platform]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :22880 | `_handle_delete_proposal` | **[platform]** | `is_admin = auth.is_admin and not getattr(auth, 'localhost_agent_id', None)` |  |
| :22881 | `_handle_delete_proposal` | **[platform]** | `if not (is_creator or is_admin):` |  |
| :22928 | `_handle_get_influencers` | **[tenant]** | `if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):` |  |
| :24657 | `_handle_post_chat_impl` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` |  |
| :24695 | `_handle_post_chat_impl` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin,` |  |
| :24749 | `_handle_post_chat_impl` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` |  |
| :30409 | `_talent_id_by_name` | **[tenant]** | `is_admin = bool(auth.is_admin) if auth else False` |  |
| :30414 | `_talent_id_by_name` | **[tenant]** | `if row and (is_admin or (row['created_by'] or '') == uid):` |  |
| :30421 | `_talent_id_by_name` | **[tenant]** | `if is_admin or (r['created_by'] or '') == uid:` |  |
| :30448 | `_extract_talent_from_text` | **[tenant]** | `is_admin = bool(auth.is_admin) if auth else False` |  |
| :30461 | `_extract_talent_from_text` | **[tenant]** | `if is_admin or (row['created_by'] or '') == uid:` |  |
| :30472 | `_extract_talent_from_text` | **[tenant]** | `if is_admin or (r['created_by'] or '') == uid:` |  |
| :30495 | `_build_single_talent_injection` | **[tenant]** | `is_admin = bool(auth.is_admin) if auth else False` |  |
| :30496 | `_build_single_talent_injection` | **[tenant]** | `if not is_admin and (t.get('created_by') or '') != uid:` |  |
| :30594 | `_build_talent_injection_text` | **[comment]** | `数据隔离规则：管理员（is_admin）看全部，非管理员只看自己（created_by=user_id）录入的。` | 注释/docstring 字样，无权限语义 |
| :30606 | `_build_talent_injection_text` | **[tenant]** | `if auth.is_admin:` |  |
| :30616 | `_build_talent_injection_text` | **[tenant]** | `if not auth.is_admin:` |  |
| :32635 | `_call_ai_api` | **[tenant]** | `allowed_knowledge_categories=None, requester_id=None, is_admin=False, team_ids=None,` |  |
| :32774 | `_wrap_reply` | **[comment]** | `#   入参与返回结构与 rag_retrieve 完全一致；四层 scope 隔离由 requester_id/is_admin/team_ids/group_ids 承载。` | 注释/docstring 字样，无权限语义 |
| :32779 | `_wrap_reply` | **[tenant]** | `requester_id=requester_id, is_admin=is_admin, team_ids=team_ids,` |  |

## 按域汇总

### [platform] 41 处 / 40 个函数

- `_handle_delete_proposal` × 2
- `_can_access_team` × 1
- `_handle_get_permissions` × 1
- `_handle_update_role_permissions` × 1
- `_handle_update_user_permissions` × 1
- `_handle_get_user_subordinates` × 1
- `_check_group_access` × 1
- `_handle_get_groups` × 1
- `_handle_update_group` × 1
- `_handle_batch_save_groups` × 1
- `_handle_delete_group` × 1
- `_handle_add_group_member` × 1
- `_handle_remove_group_member` × 1
- `_handle_get_team` × 1
- `_handle_update_team` × 1
- `_handle_delete_team` × 1
- `_handle_add_team_member` × 1
- `_handle_remove_team_member` × 1
- `_handle_post_pipe_start` × 1
- `_handle_get_pipe_progress` × 1
- `_handle_post_pipe_cancel` × 1
- `_handle_get_pipe_list` × 1
- `_handle_get_llm_usage` × 1
- `_handle_credits_admin_grant` × 1
- `_handle_credit_recharge` × 1
- `_handle_credit_recharge_generic` × 1
- `_handle_get_feishu_credits` × 1
- `_handle_post_feishu_credits_recharge` × 1
- `_handle_get_feishu_console` × 1
- `_handle_post_feishu_console_agent_op` × 1
- `_handle_get_feishu_role_content` × 1
- `_handle_get_feishu_chat` × 1
- `_handle_get_feishu_facts` × 1
- `_handle_post_feishu_fact_delete` × 1
- `_handle_post_feishu_tenant_create` × 1
- `_handle_get_token_usage` × 1
- `_handle_get_agent_calls` × 1
- `_handle_get_proposals` × 1
- `_handle_get_proposal` × 1
- `_handle_post_proposal_resolve` × 1

### [tenant] 90 处 / 65 个函数

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
- `_handle_post_kb_retry_all_failed` × 2
- `_handle_post_kb_conflict_decide` × 2
- `_handle_delete_kb_entry` × 2
- `_handle_cleanup_kb_dangling` × 2
- `_handle_post_verify_kb_index` × 2
- `_handle_post_repair_kb_index` × 2
- `_handle_put_task` × 2
- `_handle_search_talents` × 2
- `_build_single_talent_injection` × 2
- `_build_talent_injection_text` × 2
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
- `_handle_post_kb_reindex` × 1
- `_handle_post_kb_retry_embedding` × 1
- `_handle_get_kb_stats` × 1
- `_handle_get_analyzed_talents` × 1
- `_handle_get_knowledge_patterns` × 1
- `_handle_get_deals` × 1
- `_handle_get_deal_detail` × 1
- `_handle_post_kb_search` × 1
- `_handle_get_stats_compute` × 1
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

