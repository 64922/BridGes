# 02 — 学习模式教学机制混合式改版：一次性全面介绍 + 轻量进度记录
Status: ready-for-agent
Blocked by: [01](./01-learning-search-channel.md)
Covered requirements: 三次改进#1

## 背景与根因

目标形态（用户认可的 DeepSeek 参考，`../references/deepseek-share-cnn.txt` CNN 问题 / `../references/deepseek-share-transformer.txt` Transformer 问题）：思考阶段判断需要全面指南并明确覆盖维度 → "同时进行多项搜索，以覆盖不同的侧重点" → 打开 6~8 个高相关页面 → 整合成一次性、不分课时的完整介绍（引言/背景 → 核心思想 → 组件拆解 → 发展历程 → 实战要点 → 应用领域 → 学习资源 → 学习建议），全程带 `[reference:N]` 引用标注，结尾邀请就特定部分追问。

现状与目标差距（均已按当前代码核实）：当前学习模式是强制逐课时状态机——`mission_setup → micro_lesson → understanding_check → adaptation`，受阻进 `blocked`（`src/bridges/learning/teaching_gate.py:1-9` docstring；`TeachingStage` 定义在 `src/bridges/contracts/teaching.py:32`）。具体表现：

- 首响被强制拆成"固定 3 课时计划 + 第 1 课"：`compose_first_plan_and_lesson`（`teaching_gate.py:614-757`，调用点 `turn.py:2605`），教学上下文强制"必须在同一回复中交付一个版本化计划和第 1 课……不得生成第二个课时"（`turn.py:917-923`）。
- 意图分类驱动状态机分派：`classify_intent`（`teaching_gate.py:409`，调用点 `turn.py:1840`）；缺目标只回一句询问并直接 done（`turn.py:1847-1883`）。
- 检索查询被替换为单一规范主题：`micro_lesson_query`（`teaching_gate.py:556`；`turn.py:1894`、`:1900`、`:1904`），每轮只讲一个概念。
- 每轮最多一道强制理解检查题：quiz 生成（`teaching_gate.py:985-990` 区域）、作答评价 `evaluate_answer`（`:888`）、自适应 `after_answer`（`:573`）。
- 进度全量持久化到 7 张 teaching_* 表（`src/bridges/storage/database.py:1953-2063`：`teaching_plans` `:1953`、`teaching_lessons` `:1970`、`teaching_quizzes` `:1990`、`teaching_attempts` `:2009`、`teaching_assessments` `:2023`、`teaching_next_actions` `:2037`、`teaching_plan_adjustments` `:2050`），发布逻辑在 `src/bridges/learning/progress.py` `prepare_publication`（`:255`）。
- 模式合同写明分课时/测验策略：`_MODE_CONTRACTS[ChatMode.STUDY]`（`turn.py:234-253`）。

这些机制叠加导致首响信息密度低、强制分课时、强制测验，与目标形态差距是机制性的。01 修复搜索通道后，教学机制本身成为瓶颈。

## What to build

按已确认决策做"混合式"改版：

1. **首响改为一次性全面介绍**：学习目标明确时，首轮直接产出 DeepSeek 式完整介绍——多角度联网搜索（01 的多查询通道）→ 打开若干高相关页面 → 整合成文；结构覆盖背景/核心思想/组件拆解/发展或对比/实践要点/应用/学习资源与建议等维度（按主题自适应，不硬套固定目录）；全程带引用标注（编号与证据门注入的来源一一对应）；结尾邀请就特定部分追问。不分课时、不强制测验。
2. **废除强制逐课时/测验状态机**：`mission_setup → micro_lesson → understanding_check → adaptation/blocked` 状态机退役；不再自动产出计划+课时，不再每轮强制理解检查题。意图处理简化为三分支：可执行目标 → 直接全面介绍；缺目标 → 一句话询问（保留 `turn.py:1847-1883` 现有行为）；追问 → 围绕已建立目标自然深入。
3. **保留轻量学习进度记录**：只记学习目标与已覆盖主题（取代计划/课时/测验/评价的全量持久化）；追问时利用该记录自然深入（已覆盖的不重复、可顺势拓展），刷新/离开后可恢复。
4. **降级路径沿用 01**：搜索失败时按 01 的"模型知识回答 + 开头标注本轮未联网核实 + 结论降调"输出全面介绍，不拒答。

**旧 `teaching_*` 表与存量数据处理策略：保留只读，不迁移。** 7 张表结构不动，存量行保留，用于历史消息正常渲染（旧消息的 teaching JSON 投影随消息存储，`turn.py:2162`）；新逻辑不再写入这些表。不做课时语义 → 主题进度的迁移（语义不等价，迁移会制造假数据）。

**轻量进度记录的最小实现建议**：新增单表 `learning_progress`（`account_id`、`conversation_id`、`goal`、`covered_topics` JSON 数组、`source_message_id`、`created_at`/`updated_at`，按 conversation 唯一）；每轮学习模式回答落库时在消息终态事务内更新（参照 `prepare_publication` 的 commit 注入模式 `progress.py:255-266`）；`covered_topics` 用规则提取（本轮小节标题/主题词）或模型结构化输出，取实现简单者；投影进教学轮次投影供 TeachingCard 展示。

## Implementation notes

1. **`src/bridges/learning/teaching_gate.py`（状态机退役与首响编排）**：
   - 退役/删除：`classify_intent`（`:409`）中只保留"是否有可执行目标"的判定；`mission_setup`（`:434`）、`confirm_mission`（`:534`）、`micro_lesson_query`（`:556`）、`after_answer`（`:573`）、`compose_first_plan_and_lesson`（`:614-757`）、`publish_lesson_content`（`:760`）、`discard_first_plan`（`:787`）、`evaluate_answer`（`:888`）与 quiz 生成（`:985-990` 区域）整体退役。
   - 保留：证据门 `assess`（`:217-341`，01 已改裁决与降级）、`required_search`（`:344`/`:389`）、轻量进度读写；文件 docstring 状态机描述（`:1-9`）同步重写。
2. **`src/bridges/chat/turn.py`**：
   - 学习模式分支（`:1827` 起）重构：去掉计划/课时/测验分派与 `first_lesson_requested`/`formal_lesson_requested` 逻辑（`:1892-1907`、`:2598-2614`、`:2979-3011`）；首响路径 = 本地检索 → 公开搜索（01 通道）→ 证据门 → 生成一次性全面介绍。
   - `_MODE_CONTRACTS[ChatMode.STUDY]`（`:234-253`）改写：一次性全面介绍、覆盖维度、引用标注、追问自然深入、未联网核实标注（与 01 降级文案一致）、利用轻量进度。
   - `teaching_context`（`:905-927`）：删除"同窗交付计划+第 1 课"约束（`:917-923`），改为覆盖维度建议、引用编号与证据门来源对应、已覆盖主题注入。
   - 检索查询不再被 `micro_lesson_query` 替换为单一规范主题，直接用脱敏主题驱动 01 的多查询拆分。
3. **`src/bridges/contracts/teaching.py`**：`TeachingTurnProjection`（`:269-299`）精简——`plan`/`lesson`/`quiz`/`progress` 等字段退役或标记废弃，新增轻量进度投影（goal + covered_topics）；`TeachingStage`（`:32`）/`TeachingIntent`（`:49`）按新流程收敛；`TeachingPlanProjection`（`:166`）/`TeachingLessonProjection`（`:190`）/`TeachingQuiz`（`:127`）保留定义仅用于历史数据兼容渲染，不再产出。这是 API 面变更：跑 `python scripts/regenerate_openapi.py` 再生成 `openapi.json`，`packages/contracts` 类型同步，前端 `apps/web/src/lib/api.ts` 引用同步。
4. **`src/bridges/learning/progress.py`**：`TeachingProgressService`（`:69`）退役或改为只读兼容层（历史渲染）；新增轻量进度服务读写 `learning_progress` 表，发布点仍在消息终态事务内（参照 `turn.py:2999-3011` 的注入方式）。新表 DDL 进 `src/bridges/storage/database.py` 并推进 schema 版本；现有 teaching 表 DDL（`:1953-2063`）勿动。
5. **前端 `apps/web/src/components/bridges/TeachingCard.tsx`**：`stageLabel`（`:13-20`）随状态机退役删除或替换；卡片主展示改为学习目标 + 已覆盖主题 + 证据门状态 +（降级时）未联网核实标注；`statusLabel`/`evidenceLabel`（`:22-36`）沿用 01 的文案对齐。旧消息（含 plan/lesson/quiz 字段的投影）必须仍能渲染不报错。

## Acceptance criteria

- [ ] 端到端：用户在学习模式问"我想学习卷积神经网络的基础知识"且知识库为空时，系统联网搜索后给出一次性全面介绍——覆盖核心思想、组件、发展/对比、实践要点、应用、学习资源等多个维度，带引用标注，不分课时、不附带强制测验题，结尾邀请追问。
- [ ] 缺目标输入（如"我想学点东西"）仍只回一句目标询问，不触发搜索与生成。
- [ ] 追问（如"什么是感受野"）围绕同一学习目标自然深入，不重复已覆盖内容；已覆盖主题被记录，刷新/重进会话后保留。
- [ ] 搜索失败时按 01 降级路径输出全面介绍，开头标注"本轮未联网核实"。
- [ ] 存量会话历史消息（含旧 plan/lesson/quiz 投影）正常渲染；7 张 teaching_* 表存量数据保留，新逻辑不再写入；新进度只写 `learning_progress` 表。
- [ ] 契约变更同步：`openapi.json` 再生成、契约同步测试通过、前端 typecheck 通过。

## Verification

- pytest：`tests/learning/`（`test_teaching_gate.py`、`test_teaching_progress.py`、`test_teaching_service.py` 按新机制改写）、`tests/chat/` 学习模式用例（`test_teaching_chat.py`、`test_issue08_conversational_learning.py`、`test_issue18_personalized_teaching_plan.py`）、`tests/contracts/`；新增轻量进度读写与恢复、首响形态、追问深入、旧投影兼容渲染测试。
- `mypy --strict` 与 `ruff` 对改动文件干净；`python scripts/regenerate_openapi.py` 后确认 `openapi.json` 与 `packages/contracts` 同步。
- 前端 `npm run typecheck`。
- 手工冒烟：学习模式 CNN 首响（知识库为空）→ 追问"感受野" → 刷新后进度保留；打开含旧课时数据的会话确认渲染正常；断网确认降级标注。

## Non-goals

- 不改动日常陪伴模式与论文路由（paper_route）。
- 不迁移/清洗 teaching_* 存量数据，不做旧数据的进度转换。
- 不做学习计划/课时的任何替代性重做（不换个名字复活分课时）。
- 搜索通道的多查询/改写重试/并行回抓实现属 01，本 Issue 只消费其结果。
- 不动 arXiv 通道与画像系统。

## Blocked by

- [Issue 01：修复学习模式联网搜索通道并建立"未联网核实"降级路径](./01-learning-search-channel.md)——首响的多角度联网搜索依赖 01 的可用通道与降级路径。

## Comments

串行协调：本批 01/02/03 都会改 `teaching_gate.py`，01/02 都改 `turn.py` 学习模式分支与 `TeachingCard.tsx`，必须串行执行；02 基于 01 合入后的门裁决与降级信号落地，rebase 时重点核对 `assess` 分支与 `teaching_context`。

契约收敛尺度：`contracts/teaching.py` 是 API 面，前端、契约测试、`packages/contracts` 都受其影响；字段退役优先用"保留定义、停止产出、前端兼容渲染"，避免一次性删除导致历史消息渲染回归。

风险：一次性全面介绍的生成时长显著长于现状，需确认模型流式生成与 120s 总预算（`budget.py:22`）兼容；引用标注必须与证据门注入的来源编号一致，防止模型虚构 `[reference:N]`——建议渲染层按注入来源列表校验/过滤引用编号。
