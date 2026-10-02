# 19 — 生成前编译用途明确的完整画像切片

**What to build:** 回答生成前读取已提交有效信息，用于解释起点、结构和现实可行性；无关条目不注入，本轮要求优先。

**Blocked by:** 04 — 守住最终模型载荷预算与材料权威边界；11 — 解析任务指代并补回必要原文；16 — 以完整事实身份保存画像并处理并存更新；18 — 治理画像范围、有效期和语义撤回传播

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

词面召回漏掉概率入门和简短偏好；正文前 80 字裁剪会丢掉末尾排除项，生成中途加入新提取也会破坏一致性。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/用户画像/讨论记录.md](../../../docs/用户画像/讨论记录.md)。
- [docs/用户画像/提取时机与回答应用流程.md](../../../docs/用户画像/提取时机与回答应用流程.md)。
- [docs/用户画像/审查与改进建议.md](../../../docs/用户画像/审查与改进建议.md)。
- [docs/用户画像/复核脚本.py](../../../docs/用户画像/复核脚本.py)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。

## 任务内容

1. 确定任务/模式/模块/学习阶段后，从允许使用的当前账户活动、未删、未过期、证据充分且范围适用条目取得一次版本快照，不等待后台新提取。
2. 适用的默认表达偏好不要求与学科问题共享字词；背景、目标和资源约束按任务检索。先用可解释任务规则，索引/语义检索是否增加由 41 实测决定。
3. 排序按当前任务作用、来源权威、时效和证据充分度，可选零条。按共同预算整条采用，不硬截 80 字或强塞第一条，必要排除项不可裁断。
4. 给生成链传完整事实与适用条件及本轮可改变的决策；不另加模型调用来写画像应用计划，不要求用户可见回答复述内部标识。
5. 本轮自述从原文即时可用，长期偏好只是默认；专业/年级不等于已掌握，一次提问/答题不变成能力标签。
6. 同轮后续节点从同一已提交版本再选子集，不偷偷纳入后台新结果；正常重试可复用，发生删除/纠正/撤回则失效重新编译。
7. 记录采用/未采用原因并用于上下文说明；关闭使用时不读取长期画像正文，当前原文仍参与回答。

## 跨票接缝与责任

提供唯一 AdoptedProfileSlice/用途语义给 22 和 29；与 15 查询入口及 04 预算共享，同轮不得各自产生互相矛盾的采用结果。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 贝叶斯问题可取入门基础和先例后公式偏好，无词面交集仍适用；跑步爱好默认不注入。
- [x] 每天 30 分钟约束产生可执行计划，本轮详细推导覆盖简短默认且不改长期值。
- [x] 完整尾部条件保留，放不下整条排除，必要条件无法容纳时遵循 04。
- [x] 异步未完成不等待，同轮版本固定；删除后的下一次调用重新筛选有效信息。
- [x] 自述基础与答题证据分开，关画像不读取正文，采用清单与模型输入一致。
- [x] 账户隔离、切片恢复/缓存失效和无画像安全基线通过。

## 验证与交付证据

固定模型输入对比有/无/错/过时画像，检查采用条目与具体应用决策；真实回答效果在 41 验证。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 实施记录（2026-10-02）

**分支/合同版本：** 分支 `codex/19-purpose-aware-profile-slice`，基点 `main@2c003eed`；无数据库迁移（验收修复后复用既有 generation_runs.config_json 保存版本化最小采用快照）；开发和测试使用 conda `agent`（Windows，Python 3.11.15，UTF-8）。

### 实现说明

- **唯一采用快照**：`AtomicProfileService.compile_adopted_slice` 一次读取当前账户活动条目（含撤回状态），按同轮排除（本轮刚整理下一轮生效）→ 有效性门（工单 18 的撤回/暂停/完成/过期/低把握度）→ 用途规则（tier 0）或词面相关（tier 1）→ 本轮明确要求覆盖的筛选顺序，产出携带采用/排除原因、适用条件、冻结 `revocation_version` 的 `AdoptedProfileSlice`；同轮不重查仓库，删除/纠正/撤回后下一次调用自然重筛。
- **可解释用途规则**：新增 `profiles/purpose.py`（确定性、无模型调用）——任务种类（解释/计划/推荐/练习/复盘/普通）、明确要求签名（detailed/concise/answer_only/conclusion_first/example_first）、跨主题表达偏好标签、现实资源约束与背景/目标的适用决策；默认表达偏好不要求与学科问题共享字词，背景/目标/约束按任务召回，无关爱好只走词面。
- **排序与整条采用**：按（任务作用 tier、来源权威、时效、证据充分度）排序，`MAX_SLICE_ITEMS=4` 内整条采用；超限或预算不足整条排除，不硬截 80 字、不强塞第一条；`requires_confirmation` 沿用低把握度排除原因。
- **生成前接入**：`chat/turn.py` 的原子画像分支改为调用 `compile_adopted_slice`，用途由 `mode` + 本轮用户原文 + 消息模块 + 教学阶段提示确定性推导；模型块新增逐条「用途」「适用条件」「长期默认偏好」与「本轮应用」说明，保留既有固定标记且不出现内部类别；审计 `item_count` 与实际注入条目数一致。
- **覆盖语义**：本轮明确要求命中默认偏好标签时整条不采用（只影响本轮，不写回长期值）；专业/年级等自述只作为讲解起点，不生成掌握程度标签（生成链提示「不推断未提供的掌握程度」）。

### 接口/合同变化

- 新增合同：`src/bridges/contracts/profile_adoption.py` 的 `ProfileTaskKind`、`ProfileSlicePurpose`、`AdoptedProfileItem`（完整事实、适用决策、采用原因、条件、是否默认、期限）、`ProfileSliceExclusion`、`AdoptedProfileSlice`（`select_subset`、`with_items`、`to_profile_slice`）。
- 新增服务方法：`AtomicProfileService.compile_adopted_slice(account_id, *, run_id, purpose=None, current_user_message_id=None, now=None)`；既有 `compile_chat_slice` 增加 `purpose` 可选参数并改为同一采用快照的折算视图，旧调用方行为兼容。
- 聊天接线新增 `adopted_profile_context` 与 `adopted_profile_block_within_budget`（整条预算裁剪，`_remaining_input_tokens` 与工单 04 共用口径）；无 OpenAPI/数据库表变化；验收修复在既有运行配置保存 purpose-slice-1.1 快照，复用运行记录的备份、导出、账户/会话删除与失败恢复。

### 跨票接缝

- **22/29**：唯一采用对象是公开的 `AdoptedProfileSlice` + `compile_adopted_slice`；同轮后续节点从编排持有的同一冻结快照 `select_subset` 取子集（阶段一由 `profile_items` 传递同一快照折算），不得各自重编；用途语义与决策标签由 `ProfileSlicePurpose`/`applicable_to` 提供。
- **15**：查询入口（任务指代/原文恢复）与用途推导共享本轮用户原文；模块/学习阶段作为用途提示传入，选择规则可后续扩展。
- **04**：预算口径复用 `_remaining_input_tokens`，超预算整条排除；**41**：若实测需要语义/向量检索，扩展点在 `purpose.applicability` 规则层，合同不变。

### 验证结果

- 新增本票测试：`tests/profiles/test_issue19_purpose_aware_slice.py` 13 项 + `tests/chat/test_improvement19_purpose_aware_slice.py` 5 项，共 **18 passed**（覆盖贝叶斯跨词面偏好、背景/约束按任务召回、跑步排除、30 分钟约束与详细覆盖、完整条件保留、预算整条排除、关画像不读正文、采用清单与审计/模型输入一致、同轮冻结与删除重筛、账户隔离）。
- 回归：`tests/profiles` **510 passed / 1 failed**；`tests/chat` 画像相关子集 **28 passed / 1 failed**；两项失败（`test_chat_correction_uses_latest_record_and_is_idempotent`、`test_study_mode_injects_only_expected_slice`）在 `main@2c003eed` 独立复跑同名同因，属既有环境失败。
- `tests/chat` 全目录 **100 failed / 879 passed / 1 xfailed**，失败数与 main 基线一致（100），无受改文件新增失败；全量串行 `pytest`（4854 项）**269 failed / 4543 passed / 39 skipped / 1 xfailed / 2 errors**，失败集中在环境相关的既有类别（MCP/插件/生命周期/集成 CLI 等）；抽查 `tests/mcp`、`tests/plugins`、`tests/learning_projects`、`tests/retirement`、`tests/integration` 的代表性失败在 `main@2c003eed` 临时工作树逐一同名复现，均与本次变更无关；`mypy src` **98 errors** 与 main 基线一致；变更文件 ruff 零诊断；`docs/用户画像/复核脚本.py` 新增工单 19 探针运行通过并输出采用/排除/条件/长期值保留结果。
- 双轴 code-review：Standards 轴发现的未使用导出/死参已修正（删除 `DECISION_TOPIC_REFERENCE`、`is_expression_preference`、`preference_labels`、`adoption_reason` 死分支与未接线 `module_id` 参数）；Spec 轴复核的接缝与验收项见上。

### 独立验收（2026-10-02）

原交付未达标，独立审查发现用途相关性、撤回重试、冻结版本、真实预算与不可变性缺口；修复后验收通过。详见 [独立验收记录](../acceptance/19-purpose-aware-profile-slice.md)，本节及验收记录取代原交付报告中的验收边界。

- 明示学科/活动的背景、目标及资源约束经可解释主题规则筛选；通用表达偏好跨主题，通用时间约束用于计划。详细推导只覆盖简短默认，保留兼容的先例后公式。
- purpose-slice-1.1 的嵌套对象和集合不可变。同轮/正常重试从裁剪前已提交快照重选预算子集，后台新增不影响它；删除、纠正、替代、到期或缓存损坏则重编。旧表达策略不得恢复失效正文。
- 预算覆盖完整渲染块，含用途、条件和摘要；每条整条采用/排除，超长条目不阻挡后续可容纳条目，预算排除有原因。必要现实约束不足时按 04 闭锁，重试不绕过；最终裁剪与撤回后同步披露和最终审计。
- 快照保存在既有运行配置，不新增表；只存采用正文、用途语义、版本标识和排除原因，不存未采用正文或重复用户原文。旧运行无快照可重编，损坏快照可恢复。既有备份/导出/删除路径覆盖此配置。
- 22/29 的 AdoptedProfileSlice.select_subset 接缝保留；既有消费者仍用最终采用子集的 ProfileSliceItem 折算。module_id/learning_stage 参加显式主题规则；规则不能证明所有语义场景，真实抽取/回答效果仍由 41 验证。
