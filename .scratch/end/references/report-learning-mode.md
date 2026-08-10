# BridGes 学习模式管线与"证据不足拒绝回答"bug 调查报告

## 1. 学习模式完整处理管线

**入口路由**：`src/bridges/api/chat.py:705/723` `POST /chat/...send_message` → `ChatService`（只做持久化/投影）→ `TurnOrchestrator.stream_turn`（`src/bridges/chat/turn.py:1377`）。SSE 事件订阅在 `src/bridges/api/chat.py:803`。

**管线**（全部在 `src/bridges/chat/turn.py` 内，学习模式分支从 `turn.py:1827` 开始）：

1. **意图分类**：`turn.py:1840` `classify_intent`（`src/bridges/learning/teaching_gate.py:409`）。无 mission 且含可执行目标 → `ESTABLISH_MISSION`；缺目标 → 只回一句询问目标并直接 done（`turn.py:1847-1883`，不过证据门）。
2. **建立/确认 mission**：`turn.py:1884-1894` → `mission_setup` / `confirm_mission`（`teaching_gate.py:434/534`），检索查询被替换为规范主题 `micro_lesson_query`（`teaching_gate.py:556`）。
3. **本地三层检索**：`turn.py:1914` `_run_retrieval`（`turn.py:3218`）→ `LayeredRetrievalService.run_round`（附件/项目文件/知识库三层，`src/bridges/retrieval/service.py`）。
4. **决定公开来源**：`turn.py:1942` `required_search`（`teaching_gate.py:343`）：本地不足且无论文关键词 → `DUCKDUCKGO`。
5. **并行公开搜索**：`turn.py:1950-2144`，经 `_parallel_search`（`turn.py:3171`）线程池执行，阶段墙钟 = min(arxiv 10s, web 8s)（`src/bridges/chat/budget.py:28-29`）。
6. **证据门裁决**：`turn.py:2146` `TeachingTurnService.prepare` → `TeachingEvidenceGateService.assess`（`teaching_gate.py:217`）。
7. **阻断或生成**：`can_answer_reliably == False` 时**完全不调模型**，直接落库 `gap_response` 并 `done`（`turn.py:2187-2224`）；通过时才进入模型生成、发布计划/第一课（`turn.py:2599-2603`、`2979-3011`）。

## 2. 联网搜索实现与失败点（bug 根源所在）

**实现**：`src/bridges/web_search/service.py`（编排：查询脱敏、缓存、重试）+ `src/bridges/web_search/client.py`（HTTP 客户端）。

**关键事实**：`client.py:25` 用的是 **DuckDuckGo Instant Answer API**（`https://api.duckduckgo.com/?format=json`），不是网页搜索 API。这个 API 只对知名词条返回 `Results`/`RelatedTopics`（主要源自英文维基百科），对绝大多数真实查询——尤其中文查询——**返回空结果**。这是最可能的失败点。

针对本例查询的逐步推演：

- **查询脱敏破坏语义**：`service.py:256-261` `_scrub_with_categories` 用 `_TOKEN` 正则把"我想学习卷积神经网络的基础知识"切成 ≤8 字的 CJK 片段并限 8 个 token，实际发往 DDG 的查询约为 `"我想学习卷积神经 网络的基础知识"`。
- **空结果**：`client.py:377-431` `_parse_results` 只认 `Results`/`RelatedTopics`；DDG Instant Answer 对该中文查询返回空 → `service.py:434-444` 投影为 `EMPTY`（"没有找到可核实的公开网页结果"）。
- **即使 DDG 返回了结果，还有三道过滤会继续失败**：
  - 每条结果要**回抓原网页**才算 verified（`client.py:210-300`），`follow_redirects=False`（`max_redirects=0`），任何 3xx/超时/连接错误 → `fetch_failed`；
  - **聚合站被永久降级**：Wikipedia、百度百科、知乎等列入 `_AGGREGATOR_HOSTS`（`client.py:31-40`）一律标 `summary_only`（`client.py:283-292`），而证据门只接受 `verified`/`cross_verified`（`teaching_gate.py:168`）——中文知识类查询的最常见来源被制度性排除；
  - 无 verified → `service.py:445-462` 投影为 `FETCH_ERROR`/`EVIDENCE_INSUFFICIENT`。
- **无备用引擎、无查询改写**：provider 固定 DuckDuckGo（`service.py:28`），`max_retries=1`、`max_queries=1`（`service.py:50-51`），仅对可重试错误重试一次，`EMPTY` 不重试、不换查询、不换引擎。
- **次要超时风险**：`fetch_sources=True` 时最多 5 个页面**串行**回抓（`client.py:137`），每次用客户端默认 8s 超时，但阶段总墙钟只有 8s → 易触发 `_SEARCH_TIMEOUT`（`turn.py:2048-2056`）。

## 3. 证据门逻辑与"不用模型自身知识"的原因

**位置**：`src/bridges/learning/teaching_gate.py:214-341`（`TeachingEvidenceGateService.assess`）。

**阻断条件**（本例走的路径）：

- 本地无命中 → `local_reason = "当前附件、项目文件和授权知识库没有可用命中。"`（`teaching_gate.py:254-256`）；
- 搜索状态 `EMPTY`（`teaching_gate.py:288-299`）→ reason 追加"公开补充检索没有返回可用结果。"，`status=INSUFFICIENT`，`gap="没有找到能覆盖本轮目标的公开来源，暂不能可靠断言关键结论。"`；
- `_status_for`（`teaching_gate.py:1026-1037`）映射为 `TeachingCardStatus.EMPTY` → `can_answer_reliably=False`（`teaching_gate.py:1009-1012`）。

**是否存在模型知识兜底路径**：**不存在，这是刻意设计（fail-closed）**：

- 证据门不过时根本不调用模型，直接回复 `gap_response`（`turn.py:2187-2224`；文案来自 `teaching_gate.py:992-997`）；
- 即使进入生成，注入模型的教学上下文也明确禁止（`turn.py:905-909`："只能使用下列证据门允许的来源，不得用模型记忆填补缺口"）；
- 学习模式系统提示同样写明"证据不足、冲突或不可用时……不得用模型记忆补全或伪造资料"（`turn.py:243-246`）。

所以截图行为是"证据门 + fail-closed"按设计工作，**bug 不在门本身，而在它唯一依赖的公开来源通道几乎必然为空**。

## 4. 分课时/教学进度机制

**存在**。状态机在 `src/bridges/learning/teaching_gate.py:1-9`（docstring）：`mission_setup → micro_lesson → understanding_check → adaptation`，受阻进 `blocked`。合同定义在 `src/bridges/contracts/teaching.py`（`TeachingMission`、`TeachingPlanProjection`、`TeachingLessonProjection`、`TeachingQuiz` 等）。

- **计划/课时**：首课由 `compose_first_plan_and_lesson`（`teaching_gate.py:614-757`）生成固定 3 课时计划 + 第 1 课；正文发布在 `publish_lesson_content`（`teaching_gate.py:760`）；失败丢弃在 `discard_first_plan`（`teaching_gate.py:787`）。
- **作答评价/自适应**：`evaluate_answer`（`teaching_gate.py:888`）+ `after_answer`（`teaching_gate.py:573`，连续困难自动缩小概念）。
- **持久化**：`src/bridges/learning/progress.py` `TeachingProgressService`（由 `turn.py:115/1347` 注入，发布点在 `turn.py:2999-3011`）；数据库表在 `src/bridges/storage/database.py:1953-2037`：`teaching_plans`、`teaching_lessons`、`teaching_quizzes`、`teaching_attempts`、`teaching_assessments`、`teaching_next_actions`、`teaching_plan_adjustments`。mission 同时随消息的 teaching JSON 投影持久化（`turn.py:2162` `update_message_teaching`）。

## 5. 学习模式 prompt 与教学策略

- **模式合同**：`src/bridges/chat/turn.py:234-254`（`_MODE_CONTRACTS[ChatMode.STUDY]`）。策略：界定目标（概念理解/方法掌握/练习巩固）→ 参考对话内知识状态循序渐进 → 例子连接新旧认知 + 主动理解检查/适量测验 → 结尾给下一步建议；每轮先核对本地证据，不足时检索公开来源，证据不足必须明说缺口、禁止模型记忆补全。
- **教学上下文注入**：`turn.py:905-927` `teaching_context`（证据边界、可靠回答许可、首课"计划+第 1 课"同窗交付约束、缺口披露）。
- **检索/联网上下文注入**：`turn.py:592` `retrieval_context`、`turn.py:655` `web_search_context`（公开来源标记为不可信资料）。
- 前端展示：`apps/web/src/components/bridges/TeachingCard.tsx:22-52`（"暂未找到足够证据"=empty:26、"证据不足"=insufficient:33、"补充来源：DuckDuckGo"=269）与 `apps/web/src/components/bridges/WebSearchCard.tsx`。

## 根源判断

直接触发链：本地无命中（预期）→ 证据门要求 DuckDuckGo 补充 → **`DuckDuckGoClient` 使用 Instant Answer API，对中文学习类查询结构性返回空结果**（`client.py:25` + `client.py:377`）→ `EMPTY` → 门裁决 `INSUFFICIENT` → 按 fail-closed 设计输出 gap_response。

按贡献排序的根因：

1. **选错了搜索端点**：`api.duckduckgo.com` Instant Answer API 不是通用网页搜索，对绝大多数非英文维基词条查询返回空——这使"知识库无资料时联网兜底"的承诺在学习模式下事实上不可达。
2. **verified 门槛过高**：要求逐条回抓页面且禁重定向，并把 Wikipedia/百度百科/知乎永久降级为 `summary_only`（不计入证据），即使 DDG 返回了结果也大概率凑不出一条 verified 来源。
3. **无降级路径**：单查询、最多 1 次重试、无备用引擎、空结果不重写查询；8s 阶段预算 vs 最多 5 次串行页面回抓也容易超时。
4. fail-closed 的证据门只是把这个上游缺陷放大成"拒绝回答"——它本身按 ADR 设计工作。注意：用户已决策改为"模型知识兜底+诚实标注"，见 issue 01/02。

## 参考：DeepSeek 分享页提取的思考/回答全文

见 `.tmp/ds1_share.txt`（CNN 问题）与 `.tmp/ds2_share.txt`（Transformer 问题）。要点：
- 思考：判断需要全面指南 → 明确列出覆盖维度（基础概念/架构/训练/应用/学习资源）→ "同时进行多项搜索，以覆盖不同的侧重点"。
- 搜索：一轮返回 40 个网页 → 打开 6~8 个高相关页面 → 整合。
- 回答：一次性、不分课时，从多个方面完整介绍；结构为 引言/背景 → 核心思想 → 组件拆解 → 发展历程 → 实战要点 → 应用领域 → 学习资源 → 学习建议；全程带 `[reference:N]` 引用标注；结尾邀请就特定部分追问。
