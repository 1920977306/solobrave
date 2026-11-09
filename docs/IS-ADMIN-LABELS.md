# is_admin 逐处打标清单（阶段 1 前置 #1）

> 生成：`python3 scripts/label_is_admin_sites.py > docs/IS-ADMIN-LABELS.md`
> 口径：docs/SUBJECTS-PACKS-RECONCILE.md §2#1 / §5。改代码后行号会漂，重跑本脚本。
> 本次扫描：**39 处消费** = [platform] 0 + [tenant] 39；另有 framework 2、definition 1、comment 4、literal 5。

**拆主开关顺序（§2#1 红线）：先按本清单把 [platform] 消费切到 super_admin，[tenant] 消费保留 is_admin 语义（后续归 tenant_owner），每切一批跑全量回归。**

| 行号 | 所在函数 | 标签 | 代码 | 备注 |
|---|---|---|---|---|
| :1875 | `_get_effective_permissions` | **[framework]** | `if hasattr(user_or_auth, 'is_admin') and (user_or_auth.is_admin` | 权限计算核心：hasattr 探测 is_admin，是权限管理的输入 |
| :2675 | `is_admin` | **[tenant]** | `def is_admin(self):` |  |
| :3033 | `_api_token_auth_result` | **[comment]** | `★ scoped token 永远 role='user'：即使绑定 admin 用户也不放行 is_admin 检查，` | 注释/docstring 字样，无权限语义 |
| :3290 | `_require_admin` | **[framework]** | `语义对照：切换前 is_platform_admin ≡ is_admin（同读 role=='admin'），` | 通用闸门 helper 本体，消费方集中在权限/租户管理 |
| :12553 | `_handle_global_search` | **[tenant]** | `is_admin=auth.is_admin,` |  |
| :15362 | `_handle_get_memory` | **[tenant]** | `user_id=auth.user_id, is_admin=auth.is_admin,` |  |
| :16045 | `_handle_induct_to_knowledge` | **[tenant]** | `if not auth.is_admin and auth.user_id != emp_id:` |  |
| :16071 | `_handle_archive_inducted` | **[tenant]** | `if not auth.is_admin and auth.user_id != emp_id:` |  |
| :16305 | `_handle_trigger_summary` | **[tenant]** | `if not auth.is_admin and auth.user_id != emp_id:` |  |
| :16385 | `_handle_post_summary_review` | **[tenant]** | `if not auth.is_admin and auth.user_id != row['emp_id']:` |  |
| :16433 | `_handle_post_agent_knowledge_base` | **[tenant]** | `if not auth.is_admin and auth.user_id != emp_id:` |  |
| :16531 | `_handle_get_knowledge` | **[tenant]** | `if auth.is_admin:` |  |
| :16541 | `_handle_get_knowledge` | **[tenant]** | `is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :16560 | `_handle_get_knowledge_detail` | **[tenant]** | `if not ks.can_read_knowledge(doc, auth.user_id, is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :16603 | `_handle_get_knowledge_search` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` |  |
| :16645 | `_handle_post_knowledge` | **[tenant]** | `if not ks.can_create_knowledge(scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :16703 | `_handle_put_knowledge` | **[tenant]** | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :16746 | `_handle_put_knowledge` | **[tenant]** | `if not ks.can_create_knowledge(target_scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :16787 | `_handle_delete_knowledge` | **[tenant]** | `if not ks.can_delete_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :16793 | `_handle_delete_knowledge` | **[tenant]** | `deleted = ks.knowledge_delete(doc_id, is_admin=auth.is_admin)` |  |
| :16853 | `_handle_knowledge_rollback` | **[tenant]** | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :16910 | `_handle_knowledge_move` | **[tenant]** | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :16928 | `_handle_knowledge_move` | **[tenant]** | `if not ks.can_create_knowledge(new_scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :16996 | `_handle_get_kb_entries` | **[tenant]** | `if auth.is_admin:` |  |
| :17008 | `_handle_get_kb_entries` | **[tenant]** | `is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :17031 | `_handle_get_kb_entry_detail` | **[tenant]** | `if not ks.can_read_knowledge(doc, auth.user_id, is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :17071 | `_handle_post_kb_entry` | **[tenant]** | `if not ks.can_create_knowledge(scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :17261 | `_handle_post_kb_retry_embedding` | **[tenant]** | `is_admin=auth.is_platform_admin,` |  |
| :17296 | `_handle_post_kb_retry_all_failed` | **[literal]** | `is_admin=True, operator_id=auth.user_id,` | 字面量参数（ks 层系统级操作语义），非身份消费 |
| :17322 | `_handle_put_kb_entry` | **[tenant]** | `if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :17348 | `_handle_put_kb_entry` | **[tenant]** | `if not ks.can_create_knowledge(target_scope, auth.user_id, is_admin=auth.is_admin,` |  |
| :17383 | `_handle_put_kb_entry` | **[tenant]** | `is_admin=auth.is_admin,` |  |
| :17491 | `_handle_post_kb_conflict_decide` | **[tenant]** | `doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :17498 | `_handle_post_kb_conflict_decide` | **[tenant]** | `ks.kb_entry_delete(eid, is_admin=auth.is_admin,` |  |
| :17533 | `_handle_delete_kb_entry` | **[tenant]** | `if not ks.can_delete_knowledge(doc, auth.user_id, is_admin=auth.is_admin,` |  |
| :17541 | `_handle_delete_kb_entry` | **[tenant]** | `deleted = ks.kb_entry_delete(entry_id, is_admin=auth.is_admin, operator_id=auth.user_id)` |  |
| :17589 | `_handle_cleanup_kb_dangling` | **[literal]** | `stats = ks.kb_entry_cleanup_dangling(days_old=days_old, is_admin=True)` | 字面量参数（ks 层系统级操作语义），非身份消费 |
| :17622 | `_handle_post_verify_kb_index` | **[literal]** | `result = ks.kb_entry_verify_index(entry_id=entry_id, limit_per_type=limit, is_admin=True)` | 字面量参数（ks 层系统级操作语义），非身份消费 |
| :17650 | `_handle_post_repair_kb_index` | **[literal]** | `result = ks.kb_entry_repair_index(entry_id=entry_id, confirm=confirm, is_admin=True)` | 字面量参数（ks 层系统级操作语义），非身份消费 |
| :17754 | `_handle_get_kb_stats` | **[tenant]** | `is_admin=auth.is_admin, user_team_ids=auth.team_ids,` |  |
| :17775 | `_handle_get_llm_usage` | **[comment]** | `# （scoped token 已先过 _required_api_scope 闸门；其 role 恒为 user，不认 is_admin）` | 注释/docstring 字样，无权限语义 |
| :18765 | `_handle_post_kb_search` | **[tenant]** | `user_id=auth.user_id, is_admin=auth.is_admin,` |  |
| :19806 | `_handle_post_rag_retrieve` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` |  |
| :22827 | `_handle_get_proposals` | **[comment]** | `# ★ GET admin-only 收窄 (跟 resolve 硬闸 is_admin 一致, 老大 19:05 拍板)` | 注释/docstring 字样，无权限语义 |
| :24808 | `_handle_post_chat_impl` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` |  |
| :24846 | `_handle_post_chat_impl` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin,` |  |
| :24900 | `_handle_post_chat_impl` | **[tenant]** | `requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,` |  |
| :32786 | `_call_ai_api` | **[literal]** | `allowed_knowledge_categories=None, requester_id=None, is_admin=False, team_ids=None,` | 字面量参数（ks 层系统级操作语义），非身份消费 |
| :32925 | `_wrap_reply` | **[comment]** | `#   入参与返回结构与 rag_retrieve 完全一致；四层 scope 隔离由 requester_id/is_admin/team_ids/group_ids 承载。` | 注释/docstring 字样，无权限语义 |
| :32930 | `_wrap_reply` | **[tenant]** | `requester_id=requester_id, is_admin=is_admin, team_ids=team_ids,` |  |

## 按域汇总

### [platform] 0 处（已清零 ✅）

### [tenant] 39 处 / 28 个函数

- `_handle_put_kb_entry` × 3
- `_handle_post_chat_impl` × 3
- `_handle_get_knowledge` × 2
- `_handle_put_knowledge` × 2
- `_handle_delete_knowledge` × 2
- `_handle_knowledge_move` × 2
- `_handle_get_kb_entries` × 2
- `_handle_post_kb_conflict_decide` × 2
- `_handle_delete_kb_entry` × 2
- `is_admin` × 1
- `_handle_global_search` × 1
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
- `_handle_get_kb_stats` × 1
- `_handle_post_kb_search` × 1
- `_handle_post_rag_retrieve` × 1
- `_wrap_reply` × 1

