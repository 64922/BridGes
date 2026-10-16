# 14 — 接通学习档案与目标而不复制状态：验证记录

**分支：** `codex/issue-14-learning-record-integration`（基点 `ffcbe07f`，main；
即工单 13 合并后的 main）。
**环境：** conda `agent`（Python 3.11.15）；前端 Node v24.14.0 / npm 11.9.0。
**证据：** 本文 + `evidence/demo-walkthrough-20261016T111800Z.txt` +
`tools/walk_issue14_learning_demo.py`（可复跑，25 项检查全 `PASS`，`FAIL 0`）。
**可复跑：**

    python .scratch/profile-memory-redesign/validation/14-learning-record-integration/tools/
        walk_issue14_learning_demo.py --out <validation>/evidence/demo-walkthrough-<stamp>.txt

（确定性走查约 20 秒；只使用临时库 `.tmp/issue14-demo-*` 与内存组合根，不读取
或记录任何凭据与真实数据。模型调用使用仓库内既有脚本替身保证确定性；L06/L08
接口本身全部真实：真实路由、真实后台执行器、真实目标仓库与档案控制服务。）

## 0. 交付与契约变更

- **领域所有权（ADR-0042）**：引用对象是学习目标（`domain="study"`，
  `reference_id=goal_id`）。画像只存「哪个领域、哪个对象」的引用；目标状态、
  版本与记录有效性全部由学习领域实时提供，读取时不复制、不缓存进画像库。
- **页面状态操作经所属领域**：`StudyProfileLinkService.set_state` 把
  暂停/继续/完成连同页面读到的目标版本交给 `StudyGoalRepository` 事务方法
  （`pause/reactivate/complete_goal_in_transaction`，含新增的完成守卫），
  版本冲突/缺失/对象不存在抛出 `OngoingDomainReferenceError` 并映射为
  409/422/404；画像条目**不写**（无第二份状态，不双写）。
- **新合同 `learning-references-v1`**（`src/bridges/contracts/learning_references.py`）：
  `LearningReferencesProjection`（owner=`study`）列出目标与未被删除的学情判断；
  `verified` 语义 = `state=active` 且来源可读；纠正（`corrected`）、待验证
  （`pending_confirmation`）与来源不可读都如实标注，不作为已核验掌握。
- **ongoing 合同升级 `ongoing-context-v2`**（`src/bridges/contracts/ongoing_profile.py`）：
  领域引用投影与状态请求新增 `version` / `reference_version`（跨会话并发守卫）；
  新增跨域错误类型 `OngoingDomainReferenceError`。
- **近期工作上下文**：依赖指纹加入领域解析行
  （`d|item_id|domain|reference_id|resolved|state_code|version`），目标暂停/继续/
  完成、版本变化或引用不可解析都让指纹失配，读取时重建（迟到重建仍由 13 的
  epoch CAS 拒绝）。学情记录不在上下文载荷里，其纠正/删除由学习引用区**实时
  读取**反映（不落库、不缓存）。
- **组合根接线**（`src/bridges/api/app_setup_models.py`）：数据库就绪时把
  `StudyProfileLinkService` 注册为领域解析提供方 + 状态操作提供方 + 学习引用
  读取端口；未接通时 `GET /profiles/learning-references` 如实 503，页面不显示
  空学情冒充「没有记录」。
- **迁移**：无（沿用 13 的 schema 87，只新增 Python 接缝与契约）。
  `openapi.json`（322 → 323 paths）与 `packages/contracts/src/generated.ts`
  重新生成（+228 行、0 删除），契约同步测试通过。
- **前端**：记忆页新增「学习背景与学情判断」卡（只读实时；加载/空态/未接通/
  失败重试完整）；进行中事项卡提交状态操作时携带 `reference_version`，领域
  状态标签中文化；`apps/web/src/lib/api/profile.ts` 新增
  `fetchLearningReferences`。
- **500 行约束**：为保持上限，目标行解析助手抽到 `study/goals_rows.py`，
  事项投影抽到 `profiles/ongoing_projection.py`（`ongoing.py` 485 行、
  `goals.py` 457 行、新增模块均远小于 500）。

## 1. 验收标准对照

| 验收标准（票据） | 证据 |
| --- | --- |
| 自述学过、「懂了」、实际作答证据有清晰区别，阅读和单次答对不自动证明长期掌握 | 走查 [1]（自述写入个人记忆条目；引用区只列真实核验记录、`verified=true` 依据实际作答；自述不进入引用投影）；`test_issue14_learning_e2e.py::test_memory_view_separates_self_report_and_verified_learning`；契约文档与 `LearningEvidenceReference.verified` 语义；`learning-references-v1` 不含任何自述来源 |
| 继续学习使用原领域目标和进展，页面管理不造成双写 | 走查 [3]（页面暂停/继续/完成在学习领域执行、目标版本 +1，画像条目版本与自身状态不变；聊天侧续学 `link_existing_goal_in_transaction` 承接原目标与原路线 `current_node_id` 不变）；`test_issue14_learning_integration.py::test_page_state_ops_execute_in_learning_domain_without_double_write`、`test_issue14_learning_e2e.py::test_page_pause_and_chat_resume_stay_consistent_through_domain` |
| 学习纠正/删除立即使个人派生内容失效，迟到结果不恢复旧学情 | 走查 [2]（真实 L06 纠正后 `verified=false` + 用户说明；删除后引用区立即为空、持久层保持 `deleted`）；`test_issue14_learning_e2e.py::test_record_deletion_and_late_results_do_not_revive_memory_view`（删除时后台诊断未完成：迟到结果任务 `invalidated`、结果表零新增、引用区保持为空）；`test_late_diagnosis_cannot_overwrite...`（L06 既有） |
| 账户权限、本轮不用和用途规则不被桥接绕过，必要当前教学依据按自身合法用途使用 | 走查 [4]（关使用后上下文不可用、接续通道关闭）；`test_issue14_learning_integration.py::test_context_fingerprint_tracks_domain_state_changes`（usage 开关门控接续）、`test_issue14_learning_e2e.py::test_page_pause_and_chat_resume_stay_consistent_through_domain`（跨账户 404、他人不可见引用）；13 既有采用门禁回归（`tests/chat` 学习相关 41 项通过） |
| 跨会话并发不会覆盖学习领域的新状态 | 走查 [3]（陈旧 `reference_version` → 409 `ongoing_domain_reference_conflict`，目标保持并发方写入的版本；缺失版本 → 422）；`test_page_state_ops_execute_in_learning_domain_without_double_write`（事务内守卫失败重读确认、幂等重复操作不推进版本）；`test_linked_goal_resolves_live_state_and_owner_scope`（领域侧独立暂停即时生效） |
| 有真实 L06、L08 接口的完整验证记录，不能用纯替身测试标为完成 | 走查由真实路由 + 真实后台执行器 + 真实 `StudyGoalRepository` / `StudyRecordControlService` 完成（脚本替身仅替换模型以便确定性）；本记录 + 走查证据；`test_issue14_learning_e2e.py` 三项端到端；本节 §3 的完整测试与全量差分 |

## 2. 确定性走查（最终代码，20261016T111800Z）

1. **自述与核验分栏**：自述事实写入个人记忆条目；真实导师流程形成已核验
   记录；引用区 `owner=study`、`learning-references-v1`，只列该记录
   （`verified=true`、`independent_completion`），自述不出现在引用区但仍在
   个人记忆条目列表。
2. **纠正与删除**：真实 L06 纠正路由后立即 `corrected`/`verified=false`、
   用户说明原文透传；真实删除路由后引用区立即为空、持久层保持 `deleted`。
3. **领域状态操作**：组合根接通后引用实时解析（display/state/version）；
   页面暂停在学习领域执行（`paused`、版本 +1），画像条目版本与自身状态不变；
   陈旧 `reference_version` 409、缺失 422；聊天侧续学承接原目标与原路线；
   恢复后页面实时反映；再次暂停、完成后事项区不再列出、画像条目保留、
   已完成目标不进入引用投影。
4. **上下文与接续联动**：首次读取建立上下文；学习领域暂停使指纹变化并在
   读取时重建（领域状态只引用）；暂停中不进入接续候选；关使用后上下文
   不可用、接续通道关闭；重新开启并恢复目标后上下文重建、接续候选回到该事项。
5. **组合根接线**：无学习接线的组合（内存模式）如实 503
   `learning_references_unavailable`，不伪造空学情。

## 3. 自动化测试

- 新增 `tests/profiles/test_issue14_learning_integration.py`（4 项）：引用解析
  实时与账户作用域（领域侧独立暂停/完成即时反映）、页面状态操作经领域且
  不双写（409/422/幂等/条目版本守卫/完成隐藏）、上下文指纹联动（领域版本、
  开关关闭不绕过）、未接通 503。
- 新增 `tests/profiles/test_issue14_learning_e2e.py`（3 项）：真实 L06 流程下
  自述与核验分栏/纠正/来源不可用如实标注、删除后引用区立即失效且迟到结果
  不复活、页面暂停与聊天侧续学一致（跨账户隔离、陈旧领域版本 409）。
- 更新 `tests/profiles/test_issue13_ongoing_api.py`：领域引用投影断言补上新增
  `version` 字段（合同扩展的期望值更新，断言语义不变）。
- 前端：`LearningReferencesCard.test.tsx`（5 项：目标与已核验、纠正/待验证/
  来源不可读分别不冒充核验、删除后再次读取消失、未接通如实说明、失败重试）；
  `OngoingItemsCard.test.tsx`（+2 项共 10 项：携带领域版本、未解析不伪造版本）。
- 聚焦（最终代码）：`pytest tests/profiles` **763 通过**；
  `tests/chat` 学习相关 6 个文件 **41 通过**；`tests/contracts` **2 通过**；
  前端 `npm run typecheck` 通过、`npm run test:unit` **309 通过（36 文件）**、
  `npm run build` 通过。
- 全量（`-n 4 --ignore=tests/career_plan --continue-on-collection-errors`）：
  - 本分支：**328 failed / 5694 passed / 37 skipped**（12m47s）；
  - 基线 `ffcbe07f` 同命令：**328 failed / 5685 passed / 39 skipped**（12m42s）；
  - 逐条差集：**分支独有失败 0 项，基线独有失败 0 项**（失败集完全一致；
    既有 `test_secret_scan` 等环境类失败两处相同，与本票无关）；
  - 通过 +9 = 本票新增 7 项 + `tests/runtime/test_runtime_contract.py` 2 项
    （基线工作树无 web 构建产物被 `skipif` 跳过，本分支 `npm run build` 后
    运行通过；skip 39 → 37 的差额即此 2 项）。
- `ruff check` 全部改动文件通过；`mypy src` **141 错误 = 基线 141 错误**
  （无本票新增；全部位于既有文件，已在基线工作树核对）。

## 4. 决策与边界说明

- **不独立推断掌握**：投影逐字透传学习字段；「我学过/懂了」只属个人记忆
  条目；阅读、单次答对或自评不构成 `verified`。
- **不双写**：关联目标的画像条目不存目标状态；页面管理只发领域操作与版本。
- **关联/解绑**沿用 13 的 `PUT /profiles/ongoing/{id}/domain-reference` 路由
  （本票接线学习实现）；不新增链接管理 UI（13 无该 UI、本票演示也不需要；
  路由与类型接缝保留可用）。
- **失效语义**：近期上下文只有目标引用；目标变化即时重建，学情纠正/删除由
  引用区实时读取反映（无缓存）。已完成的关联目标不再进入上下文与引用区。
- **未接通如实拒绝**：无领域提供方时状态操作 422、读取 503，不伪造学习状态。

## 5. 已知限制与后续

- 走查与端到端测试中的模型响应使用仓库既有脚本替身（确定性、不触网）；
  L06/L08 接口、持久化、执行器与组合根全部真实，符合票据「真实集成验收」要求，
  真实模型调用的供应商验证不在本票范围。
- 全量测试中的既有环境类失败（如 secret scan、资源网络类）在基线与本分支
  完全一致，保持原样；不属于本票修复范围。
