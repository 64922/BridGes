# 26 — 以必要功能证据矩阵推荐 GitHub 项目

**What to build:** 项目推荐逐项说明必要功能的支持层次，区分整体与组件、实现与运行验证，并能接收论文/岗位类型化需求。

**Blocked by:** 10 — 以校园通勤贯通持久节点、收据与执行内核；12 — 主智能体理解混合入口与跨轮任务关系；15 — 按解析任务选择检索材料与模块上下文；21 — 按任务、边界与前文适配有分寸表达

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

覆盖比例与 stars 能把必要功能未知的组件误当整体方案；README 自述、静态实现与运行证据混淆。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/daily-workflows.md](../../../docs/workflow/daily-workflows.md)。
- [docs/workflow/discussion.md](../../../docs/workflow/discussion.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/v2/feasibility.md](../../../docs/v2/feasibility.md)。

## 任务内容

1. 将 parse/plan/search/read/match/evaluate/verify 登记为持久节点；保留原 idea、核心场景、必要/可选功能、技术/许可/运行限制与整体/组件目标。
2. 输入可接受既定的选定论文标识或岗位需求产物；实际跨模块触发由 37 校验，身份未确认不能宣称对应实现。
3. API 元数据/README/目录先筛，按结论选择相关入口/实现文件，记录提交版本或取得时间；每项需求标文档自述、静态实现、未确认或未支持及具体来源。
4. 必要功能不能被可选项计数或 stars 抵消；整体候选逐项说明依据和缺口，组件明确覆盖部分与集成工作。
5. 静态读取不等于实际运行，未运行不声称能跑。读取许可文件才说明复用条件，许可不可得保持未知；维护/stars 只辅助排序。
6. 普通推荐保留 2–3 个习惯，缺少整体候选可交付有用组件和不足；限流保留已完成检查，有真实恢复时刻才展示，未读候选不伪装核实。
7. 已有用户可见解读调用接入 21、04；关键实现断言不足/复杂比较按风险复核，返回核验产物而非模块直接终态。

## 跨票接缝与责任

拥有 GitHub 功能矩阵与版本证据；24/28 的产物身份接口共用 08，37 负责跨模块依赖。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 必要功能未支持时不因高比例或 stars 认定整体适配。
- [x] README 自述/静态实现/运行未验证在输出中明确区分，许可未知不宣称自由复用。
- [x] 相关实现文件定位到版本和读取范围，来源不足仅给候选/缺口。
- [x] 普通目标真实数量、限流部分交付和局部恢复符合预算/收据合同。
- [x] 来源需求不被改写，输出矩阵/借鉴点/局限和引用可查。

## 验证与交付证据

覆盖 A17–A18、必要功能假阳性、README 夸大、许可缺失和限流；真实 API/文件可得性在 42。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 执行与验收记录

### 2026-10-03：实现、两轴评审修复与验证

- 交付分支 `codex/26-github-requirement-evidence-matrix`（工作树 `.worktrees/26-github-requirement-evidence-matrix`，基点 main `806600ce`）；阻塞票 10/12/15/21 已用 `git merge-base --is-ancestor` 核对带在 main。
- 环境：Windows + conda `agent`（Python 3.11）；确定性替身只证明机制，真实 GitHub API/文件可得性与真实模型解读体验按 42 验证。
- 内核化（`src/bridges/github/kernel.py` 新增）：parse/plan/search/read/match/evaluate/verify/present 登记为持久节点（`github-project-recommendation`/`github-recipe-v1`）；必检门 `github.required_matrix`（必要行必须有支持层次；整体覆盖不得有未支持/未确认必要行）、`github.version_sources`（所有命中行可定位来源与读取范围）、`github.license_disclosure`（无许可证据不得给满足结论）；重试按收据/产物复用已完成节点，不重复外发检索与读取。
- 合同（`contracts.py`）：新增 `GithubRequirementKind/SupportLevel/VersionEvidence/SourceKind/RequirementInput/ConstraintSet/SearchPlan`；`GithubFeatureMatch` 增 kind/support_level/sources/runtime_required；`GithubRecommendation` 增 required_feature_count/required_supported_count/runtime_verified/version/matrix_note；投影增 optional_features/constraints/requirement_source/identity_note；`GithubRepositoryEvidence` 增 readme_sha/version。无数据库迁移（持久状态走既有 `node_receipts`/`node_artifacts` 内核表）。
- 判定（`lexicon.py`/`parsing.py`/`ranking.py`）：必要/可选/约束三类矩阵行；WHOLE 只在全部必要行达到文档自述或静态实现且无阻断约束时成立（比例、可选项、stars 均不抵消）；运行类条件永远未确认；许可未知保持未知，点名与仓库许可规范名一致才成立（LGPL/AGPL 不再被 GPL 子串误判为满足）；「非必需」不再被误登记为排除词；技术条件命中行也给出元数据/README/实现来源。
- 版本证据（`inspecting.py`）：有实现证据时 best-effort 读一次默认分支提交（core 桶，失败不阻塞），否则降级 README/文件 blob 或取得时间；实现文件来源带 commit_sha 与读取范围。
- 类型化需求：`GithubProjectsService.run(requirement=...)` 与父图 `run.config["github_requirement"]` 接缝；`identity_confirmed=False` 时投影 `identity_note` 明确「不把仓库断言为它的实现」。
- 交付与前端：service 由内核驱动（澄清/停止/失败/限流/局部恢复投影重建）；固定正文渲染器按支持层次与类别输出并附来源定位；`GithubProjectsCard` 同步支持层次、可选/约束行、身份说明与版本提示。
- 两轴评审（标准＋规格并行子代理）发现与处置：
  - ①固定正文渲染器仍按「已覆盖」输出、与新支持层次矛盾 → 更新 `_feature_line` 并附来源；
  - ②技术/运行/排除约束行缺来源 → 技术命中行补来源，版本门覆盖到全部命中行；
  - ③GPL 与 LGPL/AGPL 子串误判 → 抽取与匹配都改为最长规范名一致；
  - ④「非必需」被登记为排除词「必需」→ 可选标记整词跳过；
  - ⑤组件路径未说明集成工作 → 正文补「集成工作需自行完成」；
  - ⑥重试复用 present 产物时借鉴角度丢失 → 交付从产物 payload 回填；
  - ⑦`FAILED_QUERY_STATUSES` 在 kernel 重复 → 收敛到 `searching.py` 单一来源。
- 未采纳（判断项）：门处理器部分分支依赖生产者不变量（保留为跨序列化的独立核验，不依赖生成者自辩）；`_digest` 第三处副本（与 commute/study 内核同形，跨模块抽公共件超出本票接缝）；排除词硬剔除保留为「用户限制」处理（含回归测试）。
- 验证：
  - `tests/github` 全量（排除 2 个环境性历史失败）：72 passed / 2 deselected；其中新增 `tests/github/test_github_requirement_matrix.py` 14 项（矩阵、约束、版本来源、类型化身份、重试复用收据、许可规范名、非必需边界、固定正文渲染、借鉴角度回填）。
  - 定向回归：`tests/github`、`tests/chat/test_improvement15_{module,task_materials,manifest}_acceptance.py`、`tests/kernel`、`tests/contracts/test_openapi_sync.py` 合计 140 passed / 2 deselected。
  - `ruff check src/bridges/github tests/github` 全过；`mypy src/bridges/github` 零错误；全仓 `mypy src` 98 项与 main 逐条一致（0 新增，忽略行号）。
  - 前端：`tsc --noEmit` 通过；`vitest run` 25 文件 220 项全过（`generated.ts` 由最新 openapi 重生成，顺带补齐 main 上过期契约；`StudyProgress.test.tsx` 夹具同步补字段）。
  - 契约：`openapi.json` 已再生成且 `test_openapi_sync.py` 通过（main 上该测试原本因过期契约失败）。
- 已知限制：`test_github_module_flow.py` 的 `test_reference_to_prior_paper_turn_uses_its_original_phrase` 与 `test_plain_chat_without_the_module_never_starts_github` 在本机 main 与分支同样失败（环境性，已 deselect）；真实额度/文件可得性与真实解读体验按 42 验证。
- 状态：实现与两轴评审修复完成，提交后等待人工验收。

### 2026-10-03：独立验收接管（失败恢复复用与父图核验绑定）

原交付未完全达标；接管审查确认两个 P1 阻断项并修复，新增 3 个恢复回归测试。

**缺陷与修复**

- P1-1 失败恢复不可复用：部分失败（检索超时/限流、读取限流）的产物没有失效标记，重试命中「已完成」收据后直接跳过，不会真正补发缺口；读取节点即使已有候选证据也会全量重发。修复：`kernel.py` 将 `github.search_repositories` 能力版本升到 `github-search-v2`；`_run_search`/`_run_read` 把部分失败产物标为 `ArtifactTrust.INVALIDATED`、收据 `COMPLETED` + `detail={"partial": true, ...}`，已取得的查询记录/候选/仓库证据保留；新增 `_resume_payload`（精确输入键，search 要求 partial）与 `_resume_evidence`（同 plan hash 回退最近 read 产物），重试只补未完成查询与未读候选；`inspecting.py` 的 `inspect_candidates` 接收 `resume_evidence`，已完成深入核查的候选直接复用，README 状态已定时只补深入核查。
- P1-2 父图核验未绑定真实产物：原 `verify_delivery_projection` 不沿真实依赖链复算，旧核验产物或伪造投影可为新结果背书。修复：`GithubDelivery` 记录 `present_artifact_id`；`verify_delivery_projection` 从 present 产物沿 `input_deps` 走链，核对配方 ID/版本、产物类型、能力版本、会话、任务引用、`verify_hash`、依赖集合与配方声明一致、上游 ID+内容哈希吻合，要求链覆盖全部 8 个配方节点，跨运行回填时本轮 COMPLETED 收据产物（INVALIDATED 除外）内容哈希必须与链上同节点一致，最后按链重建投影逐字段比对；`chat/graph.py` 的 `_verify_github_delivery` 在真实核验产物存在且节点/会话/QUALIFIED 吻合后才调用链核验，`_persist_github_delivery` 在同一事务内复核租约/任务版本/停止后统一提交。

**新增与回归测试**

- 新增 `tests/github/test_github_acceptance_recovery.py` 3 项：检索超时重试真正重发查询且 partial 产物 invalidated、成功后可复用；三候选第三个限流重试只外发 `/repos/demo/third/readme` 与 `/repos/demo/third/contents`（检索与已完成候选零重复外发）；重放历史 partial 交付被拒（`github_delivery_unverified`、`github_projects` 为空）。
- 原交付的 `tests/github/test_github_acceptance_{evidence,gates,insights}.py` 与扩展的 `test_github_requirement_matrix.py`/`test_github_module_flow.py`（含父图 HTTP 级交付核验：真实核验提交、无核验拒绝、投影篡改拒绝、同会话旧核验不能背书新任务）全部通过。

**验证结果**

- `tests/github`：113 passed / 2 failed。两个失败（`test_reference_to_prior_paper_turn_uses_its_original_phrase`、`test_plain_chat_without_the_module_never_starts_github`）已在 main@806600ce 临时工作树复现同名同因，属既有基线，非本票引入（原记录「环境性 deselect」结论成立）。
- 组合回归（`tests/github` + `tests/chat/test_improvement15_{module_acceptance,task_materials,manifest_acceptance}.py` + `tests/kernel` + `tests/contracts/test_openapi_sync.py` + `tests/lifecycle`）：244 passed / 7 failed；7 项失败全部在 main 基线复现（2 GitHub + 5 lifecycle 注册 409），无本票新增失败。
- 契约：`openapi.json` 修正为在测试环境内生成（含 `/_test/*` 路由），`tests/contracts/test_openapi_sync.py` 2 passed；`generated.ts` 与 HEAD 一致，无残留漂移。前次工作区版本是在测试环境外生成、丢掉 `/_test` 路由的错误产物。
- 静态：`ruff check src/bridges/github tests/github` 全过；`mypy src/bridges/github --follow-imports=silent` 12 文件零错误；`chat/graph.py`/`chat/repository.py` 的 4 条既有告警与 main 逐条一致。
- 前端：`tsc --noEmit` 通过；`vitest run` 25 文件 220 项全过。
- 无数据库迁移（持久状态沿用既有 `node_receipts`/`node_artifacts`，schema 65）；真实 GitHub API/文件可得性与真实解读体验按 42 验证。


