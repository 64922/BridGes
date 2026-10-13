# 07 — 系统性目标与简短学习路线

**What to build:** 用户说「带我学线性代数」时，导师提出简短可调整路线并开始必要教学；目标与进展持久保存，单个具体问题仍保持轻量。

**Blocked by:** 02 — 纯文字启动与流式局部辅导。

**Status:** ready-for-agent

**发布状态：** 用户已确认拆分与依赖；本票已实施并完成验证，新学习模式尚未上线。

**对应合同：** D02、D03、D08、D09；AC06；C01。

## 用户演示

1. 输入「我想学线性代数，主要为了理解机器学习里的矩阵」，导师理解用途。
2. 已知基础足够时直接给简短路线，缺少决定教法的信息时只问一个必要问题。
3. 用户要求调整顺序或深度，路线修订有依据，原学习目的不被更改。
4. 刷新后目标、当前知识点与路线版本仍在详情中；问单个概念不会被迫新建课程。

## 实施范围

1. 引入账户级长期学习目标，独立于会话 ID；保存用户目标原话、可观察结果、明确范围和约束。
2. 当前会话以引用关联目标，复用既有任务版本/执行能力，不修改通用跨轮任务的会话归属语义。
3. 区分系统性目标与局部问题。依据用户明确自述和必要探查形成轻量路线，不统一先做入门考试，不自动假定已有基础。
4. 规划职责只在新系统目标和确需修订时调用结构化能力；普通局部提问不每轮运行完整规划。
5. 路线包含简短知识点顺序、前置关系、当前节点、版本及修订理由。它是可改的导师方案，不能被当成用户不可更改的要求。
6. 用户可改变顺序、深度或当前节点；这些操作产生明确修订，不强制每一步点击批准，不用固定课程完成率替代进展。
7. 第一次带学沿 02 导师执行，路线创建和当前进展绑定原子提交；失败/停止不能宣称路线已保存或目标已完成。
8. 05 尚未完成时仍可基于用户目标开始；已核验档案的选择和路线自适应由 09 接入，不以档案缺失阻塞规划。

## 接口、持久化与界面

- 目标的创建、读取与修订校验账户、来源和预期版本，重复首轮不多建目标。
- 保存已明确目标与助手路线方案的来源区别，猜测不能成为用户约束。
- 顶部显示当前目标/知识点，详情按需展示路线；无路线、规划中、失败和版本冲突反馈真实。
- 路线未验证的内容不显示已掌握，用户跳到某节点不等于前面内容完成。
- 新目标及路线完成迁移、导出、备份、账户删除和历史版本读取。

## 范围边界与依赖理由

02 提供已有学习入口和执行链路。05 不是前置，缺少档案也可开始系统性学习。08 负责跨会话恢复/切换，09 负责基于有效档案调整。不得恢复课程工作台、学习项目或退役资料推荐模块。

## 验收标准

- [x] 系统目标拥有独立、账户范围的身份和用户原话来源。
- [x] 局部问题不被强制创建长期目标或完整课程。
- [x] 基础不明时只澄清必要信息，路线不基于无依据能力推断。
- [x] 路线简短可改，用户目的和明确限制始终保留。
- [x] 顺序/深度修订有版本和理由，跳节点不算前面已掌握。
- [x] 普通提问不每轮触发完整规划，角色调用均计入预算。
- [x] 创建与进展原子一致，重复请求、失败、停止和冲突不虚报成功。
- [x] 顶部与可展开路线贯通，刷新后保持状态，跨账户访问拒绝。
- [x] 新目标生命周期、生成合同和 C01 审查完成。

## 验证与交付证据

- 对比单题、宽泛领域、明确用途与缺基础四种请求，捕获真实服务链路的目标创建和规划调用。
- 验证调整先后、修改深度、跳节点、重复首轮、规划失败及路线提交冲突。
- 证明通用任务仍属于原会话，长期学习目标没有通过改会话 ID 实现。
- 提交从新目标到首个教学节点的桌面旅程，以及存储/导出/删除检查。

## Comments

2026-10-13：系统目标与路线切片草稿，待拆分确认。

2026-10-13：用户确认「按此发布」，本票发布到本地问题跟踪器。分诊状态表示信息齐备；须待直接阻塞票验收完成后实施，本轮未开始编码。

2026-10-14：工单 07 在独立分支 `codex/issue-07-goals-and-learning-route` 实施并完成验证（依赖票 02 的实现已合入 main `101ccaa4`；与本票并行交付的 03 合并后，本票迁移重编号为 v72 对齐，见交付记录）。

- **实现与所有权：** 新增账户级学习目标与导师路线：`bridges.study.goals.StudyGoalRepository`（按账户+会话读取当前目标/路线、创建、带 expected_version 守卫的修订写入）、`bridges.study.goal_intent`（区分系统性目标与调整请求的词表）、`bridges.study.goal_planning`（`StudyGoalPlanner` 经统一网关 `qwen_structured_output` 调用，任务名 `study.goal_planning`，输出经 `StudyGoalPlan`/`StudyRouteDraft` 校验：节点唯一、前置只引用本路线、当前节点在路线内、节点数 ≤8；失败返回 None 不伪造）、`bridges.study.goal_turn.StudyGoalTurnService`（按 create/build_route/revision 决策规划、渲染导师提示词区块、终态提交）。目标独立于会话 ID：`study_goals` 以 `goal_id` 为身份、`account_id` 为范围、`source_message_id` 记录用户原话来源；会话经 `study_conversation_goals` 引用目标；路线修订保留历史版本，不修改跨轮任务的会话归属。
- **触发与预算：** 只有新系统目标（无目标 + 明确学习意图）或确需修订（有路线 + 调整请求）才调用规划角色；普通局部提问与既有目标下的常规追问零规划调用。规划经统一网关与运行预算账本计数（与 02 导师、03 验证规划同一账本），`RunModelQuota` 与运行配置沿用统一路径。规划失败如实降级：仍按用户原话创建目标但不建路线，导师提示词明确「本次未能形成学习路线」且禁止声称已保存路线；下一轮恢复后补建路线且不重复建目标。
- **原子提交与冲突语义：** `persist_study_progress` 终态回调在消息 streaming→done 的同一事务内先提交 03 验证证据与局部进展、再提交目标/路线；停止、失败不进入回调，不虚报。路线修订以 `expected_version` 守卫（`INSERT ... SELECT ... WHERE MAX(version)=expected`），过期规划结果被拒绝且不覆盖新版本，返回 False 由调用方如实跳过。
- **接口与合同：** `ChatConversationProjection` 新增可空 `study_goal`（`StudyGoalProjection`：goal_id、statement 原话、observable_result/scope/constraints、status、version、source_message_id、created_at/updated_at、route）；`StudyRouteProjection`（version、nodes（node_id/title/prerequisites/depth，不含掌握状态）、current_node_id、revision_reason、origin∈{assistant_proposal,user_request}、created_at）与 `StudyRouteNode`。`openapi.json` 与 `packages/contracts/src/generated.ts` 以纯追加方式同步（openapi.json 与合并后应用输出逐字段一致；generated.ts 按既有先例只追加本票类型块）。
- **持久化与生命周期：** 合并后 `SCHEMA_VERSION` 为 72：v71 为 03 的待答问题/证据，v72（本票）新增 `study_goals`、`study_goal_routes`（PK account_id+goal_id+version，保留修订历史）、`study_conversation_goals`（账户+会话复合主键），全部登记 `REQUIRED_TABLES`/`REQUIRED_TABLE_COLUMNS`；`ACCOUNT_TABLES` 按依赖顺序接入（会话引用与路线版本先于目标删除）、导出类别 `study_goals`、逻辑清单、快照备份与恢复、账户删除均已接入并用测试覆盖。旧库经启动迁移升级，无回填。
- **前端：** `StudyProgress` 在有目标时顶部固定显示「目标：<用户原话>」与「当前知识点：<当前节点>」，路线在 `<details>` 中按需展开（版本号、逐节点顺序、前置名称、修订依据），明确「路线是可调整的导师方案；跳到某节点不代表前面的内容已完成」，不渲染任何「已掌握」宣称；无路线时如实显示「学习路线尚未形成」；与 03 的待答问题展示在同一组件中共存（目标视图同样显示待答状态）。`page.tsx` 透传 `study_goal`。`StudyProgress.test.tsx` 新增 3 项目标/路线用例，`apps/web/e2e/issue07-goals-and-learning-route.spec.ts` 为桌面旅程（真实 Web+API 进程、API 响应确定性替身）：目标与当前知识点可见、路线展开逐节点、无掌握宣称、刷新后保持。
- **验收标准证据：** ①目标身份/范围/原话来源：`goal_id` 稳定、`statement` 等于用户输入、`source_message_id` 非空，账户删除与跨账户读取均拒绝；②局部问题（「互斥是不是独立？」）不建目标、不调用规划、三表零行；③缺基础（「带我学线性代数」）只问一个必要问题（提示词只含该问题且指示「只问这一个必要问题」），回答后补建路线；④路线简短（≤8 节点）且修订时 `statement` 与目标 version 不变，可观察结果/范围/限制只来自用户明确自述；⑤顺序调整产生 v2（origin=user_request、修订理由）、跳节点产生 v3（current_node_id 更新），节点投影无掌握字段，导师提示词禁止把跳节点当作已完成；⑥普通提问后规划载荷数不变；首轮账本 `model_calls_used` 计 3（目标规划 + 03 验证规划 + 流式）；⑦重复首轮幂等重放不重复建（1,1,1）、流式失败消息 error 且三表零行、重试后仅（1,1,1）、停止不写进展不建目标、过期版本提交返回 False 且当前路线保持 v1；⑧Playwright 旅程与刷新保持通过，跨账户对话详情 404；⑨迁移、导出、删除、备份恢复、合同同步与 C01 见下。
- **C01：** 本票提示词与合同按 BridGes 需求自主设计（`goal_planning._PLANNER_SYSTEM_PROMPT`：用户目的与明确限制为权威事实、不统一先做入门考试、路线 ≤8 节点、只输出 JSON），未复制参考项目代码或提示词；无新增依赖。
- **合并与冲突处理：** 本票基点 `101ccaa4`；实施期间 03 先合入 main（`4fe0f063`），双方同用 v71 并共同修改 `database.py`/`catalog.py`/`service_projections.py`/`service_stream.py`/`contracts/chat.py`/`StudyProgress.tsx`/`page.tsx`/`openapi.json`/`generated.ts` 等。分支内先合并 main（`ca90dc55`）：本票迁移重编号为 v72、`SCHEMA_VERSION=72`、v70 测试断言沿用 03 的 `>=70` 写法、03 的 v71 测试断言放宽为 `>=71`；`service_stream` 同一终态回调同时提交验证证据、局部进展与目标/路线；`StudyProgress` 目标视图与待答问题共存；两个合同产物以纯追加方式合并后与合并应用输出一致。本票测试替身按任务名区分 `study.goal_planning` 与 03 验证规划（共用同一结构化能力），避免脚本互相消费。
- **验证（conda `agent`，Python 3.11.15）：** 合并后聚焦集 52 项通过：`test_issue07_goals_and_learning_route.py`（8）、`test_issue07_goal_planning.py`、`test_schema_v72_study_goals.py`、`test_schema_v71_study_verification.py`、`test_schema_v70_study_local_progress.py`、`test_issue03_embedded_verification.py`、`test_issue02_text_tutor.py`、`test_openapi_sync.py`。合并树全量 pytest（`-n 4`）345 失败/5761 通过/4 收集错误，与合并前 main 基线（349 失败）逐条对比：本票失败集合为基线严格子集，新增失败 0 项（基线 20 项 flaky 在本次通过）；既有失败（mcp/plugins/career 退役模块等）与基线一致。
- **静态检查：** 改动文件 Ruff 仅剩 `database.py:1671` 一项既有 E501（blame `f99de341`，非本票改动）；改动模块 mypy 无新增报错。
- **前端验证（Node v24.14.0）：** `tsc --noEmit` 干净；`vitest run` 34 文件/263 项通过（含本票 3 项与 03 用例）；`next build` 生产构建通过；`next lint` 仅 3 条既有 img/Hook 警告；`npx playwright test e2e/issue07-goals-and-learning-route.spec.ts --project=chromium` 1 项通过（Chromium 本地安装）。
- **剩余限制与后续：** 桌面旅程的模型回答由 API 替身提供，真实服务链路（目标创建、规划调用、原子提交、预算）由 pytest 真实栈覆盖；编辑深度（而非顺序/节点）的修订依赖模型返回修订路线，行为与顺序修订同一路径；跨会话恢复/目标切换由 08 接入、档案自适应由 09 接入，本票不代其验收；全仓既有基线失败不属于本票。

2026-10-14（交付记录）：本票提交 `c398ff78`（22 文件，含分支内合并 main 的 `ca90dc55`），以 `--no-ff` 合入 main，合并提交 `78e9889ca24a1c4a6fb9b594ab2d7943fe3439fd`；合并前经代理 `http://127.0.0.1:7890` fetch 确认远端 main 与本票合并前基点同为 `4fe0f063`。合并后在 main 复跑上述 52 项聚焦测试全部通过。推送：`git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 push origin main`（`4fe0f063..78e9889c`），`git ls-remote origin refs/heads/main` 与本地 main、origin/main 三方一致为 `78e9889c`。随后删除本票 worktree 与本地分支 `codex/issue-07-goals-and-learning-route`（最后为 `ca90dc55`），`git worktree prune` 后仅剩主工作树；其他任务的分支未触碰。
