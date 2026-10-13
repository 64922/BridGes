# 03 — 嵌入式理解验证与即时纠错

**What to build:** 导师在关键位置提出一个理解检查，识别用户实际作答、求助与新提问，给出即时引导或纠错，并保存带提示程度和评价依据的学习证据。

**Blocked by:** 02 — 纯文字启动与流式局部辅导。

**Status:** ready-for-human

**发布状态：** 用户已确认拆分与依赖；本票已实施并完成验证，新学习模式尚未上线。

**对应合同：** D01、D05、D10；AC01–AC05、AC08；C01。

## 用户演示

1. 用户把「互斥」解释为「互不影响」，导师选择一个具体情境探查理解。
2. 当前只呈现一个需要回答的问题，用户可回答、请求提示或问为什么。
3. 真正回答后得到针对具体推理环节的反馈；请求提示得到帮助，不被判错。
4. 在提示后完成与独立回答分别记录，刷新后待答问题和依据仍一致。

## 实施范围

1. 将开放探查与有明确评价要求的小练习分开。开放问题保存理解线索，不能虚构唯一正确答案或分数。
2. 正式练习展示前固定知识上下文、评价要点、可接受等价表述、关键误解和必要假设；数值或推导答案按已登记能力核验，不在作答后改标准。
3. 保存待答问题与展示消息、局部进展/目标引用、依据版本和提示历史的绑定。当前问题变化后，旧回复或旧重试不能判到新题上。
4. 区分作答、提问、要提示、要求解释、岔题和跳过意图；本票识别这些关系，04 完成明确跳过与收尾动作。
5. 导师一次只推进一个检查，按当前困难选择提示、反例、补讲或应用；连续卡住时调整粒度，避免重复同类追问。
6. 即时反馈与正式诊断分开。保存实际回答和辅助程度，不直接写长期掌握，也不把阅读或「懂了」写成作答证据。
7. 证据引用实际消息与资料，保留支持/缺失信息和不确定性，供 05 诊断、10/11 资料与查证扩展消费。

## 接口、持久化与界面

- 待答问题的生成、展示和提交必须与该轮消息状态一致；停止和提交失败不提前激活未展示的问题。
- 客户端只看到已经展示的问题和实际反馈，私有评价依据及未问问题不泄露。
- 用户请求提示时记录确实提供的提示，不以模型自称「独立作答」覆盖实际帮助记录。
- 等价表述按含义判断，不因措辞不同扣分；无法判断时保留待确认，不以格式失败判学生错。
- 提供当前问题、帮助状态与重试反馈；新增证据的迁移、导出、删除和来源失效同时交付。

## 范围边界与依赖理由

02 提供完整消息与局部进展链路。本票拥有即时验证与证据，不拥有账户级学习档案；05 负责正式后台诊断。04 依赖本票的待答问题语义实现用户主动解除等待。

## 验收标准

- [x] 一个关键位置只呈现一个需作答问题，不每轮机械出题。
- [x] 正式练习的评价依据在展示前确定，作答后保持版本与含义不变。
- [x] 提问、要求再讲、要提示和岔题均不被当成错误作答。
- [x] 本次独立完成、提示后完成、未验证和无法判断的证据语义有区分。
- [x] 即时反馈没有直接晋升成长期掌握或总体能力标签。
- [x] 同义正确解释被接受；缺少依据或核验失败保留待确认。
- [x] 重试、停止、刷新与迟到结果不串题、不重复记账。
- [x] 私有评价依据不进入用户投影，跨账户不能读写问题与证据。
- [x] 证据与来源版本可供后续诊断使用，生命周期与合同同步完成。
- [x] C01 审查通过，教法与提示词按本项目合同重新设计。

## 验证与交付证据

- 覆盖概念误解、正确等价表述、错误推理、部分回答、求助和服务失败。
- 用同一回答分别放在有提示与无提示前文中，验证证据不会混为一谈。
- 模拟旧题重试、停止后迟到生成、提交回滚和页面刷新，核对待答绑定及实际消息。
- 交付完整用户旅程、评价依据冻结证明、API 私有字段检查和必要存储迁移记录。

## Comments

2026-10-13：嵌入验证与证据切片草稿，待拆分确认。

2026-10-13：用户确认「按此发布」，本票发布到本地问题跟踪器。分诊状态表示信息齐备；须待直接阻塞票验收完成后实施，本轮未开始编码。

2026-10-14：本票在独立分支 `codex/issue-03-embedded-verification-and-feedback`（基点 main `101ccaa4`，即依赖票 02 的交付合并记录）实施并完成验证。

- **实现与所有权：** 新增 `bridges.study.verification`，拥有学习对话的即时验证：`StudyVerificationPlanner` 在模型流式开始前完成一轮确定性规划（作答识别、评价、下一步动作与出题草稿），`StudyVerificationRepository` 在消息成功终态与消息同事务提交待答问题激活、作答证据、提示记录与旧题解除。`TurnOrchestrator.stream_turn` 新增 `_prepare_study_verification`：将规划指令追加到系统提示，只影响导师路径；规划失败或能力未注册时静默降级（回答照常、不写验证数据）。
- **规划与归一化：** 规划器用 `qwen_structured_output@1`（temperature 0、固定 JSON Schema）输出 `action/intent/outcome/check_*`；`_normalize_plan` 确定性归一化：无待答问题时才允许出题；存在待答问题时仅「评价并推进」或 `intent=skip` 可提出下一题；正式练习在连续卡住（尾部 ≥2 次部分完成/误解或提示数 ≥2）时被抑制，开放探查仍可提出；`pose_check` 与 `evaluate` 可同轮发生（posed 为旁路字段）。
- **依据冻结与提示史：** 正式练习展示前冻结知识上下文、评价要点、可接受等价表述、关键误解、必要假设与预期答案（`basis_json`），进展示事务后不可变；提示史在提示轮实时记录；作答评价读取冻结依据而非规划器当轮输出。
- **意图与证据语义：** `StudyUserIntent` 区分作答/提问/要提示/要求再讲/岔题/跳过；非作答意图不判对错、不写证据；证据语义 7 值（independent_completion / hinted_completion / partial_understanding / misconception / undetermined / unverified / understanding_clue），语义由「冻结评价 + 实际提示史」推导，不以模型自称独立完成覆盖帮助记录；跳过与替换未答问题时写 `unverified` 并解除待答。
- **接口与合同：** 公开合同新增 `StudyPendingCheckProjection`（仅问题、知识上下文、帮助状态、版本、展示消息与时间）与 `ChatConversationProjection.study_pending_check`（仅学习模式非空）；`openapi.json` 以最小增量同步（保留既有键序），`packages/contracts/src/generated.ts` 由 openapi-typescript 7.13.0 重新生成，合同同步测试通过。
- **持久化与生命周期：** `SCHEMA_VERSION` 升至 71，新增 `study_pending_checks`（账户/会话复合主键、依据与提示史、展示消息绑定、版本约束）与 `study_evidence`（`UNIQUE(account_id, answer_message_id)` 防重复记账、会话级联删除）及 `idx_study_evidence_conversation`；登记进 `REQUIRED_TABLES`/`REQUIRED_TABLE_COLUMNS`/`REQUIRED_INDEXES`、`ACCOUNT_TABLES`、导出类别「学习验证与证据」与逻辑清单；旧库经启动迁移升级，无回填。
- **重试与幂等：** 评价快照随运行配置（`run.config["study_verification"]`）冻结；重试经 `service_retry` 继承快照并重放原计划（不重新规划），旧题旧答不判到新题；延迟/停止/失败路径不激活问题、不写证据；重复提交由 `answer_message_id` 唯一约束兜底。
- **前端：** `StudyProgress` 新增待答问题与帮助状态展示（提示后完成会标注按提示记录）；`page.tsx` 透传 `study_pending_check`。
- **验收标准证据：** ①`test_one_pending_check_at_a_time_and_no_mechanical_question` 与指令「唯一需要作答的问题」；②`test_formal_check_freezes_basis_and_records_independent_completion` 断言冻结依据与作答后不回改；③`test_non_answer_intents_are_not_judged` 参数化覆盖提问/要提示/再讲/岔题；④`test_same_answer_differs_with_and_without_hint` 区分独立完成与提示后完成，`test_equivalent_expression_accepted_and_undetermined_kept_unconfirmed` 保留待确认；⑤证据只写 `study_evidence`，不写长期掌握或能力标签；⑥同义表述接受、核验不清保留 undetermined；⑦`test_failed_turn...`/`test_stopped_turn...`/`test_old_answer_retry...` 覆盖失败、停止与迟到重试；⑧`PRIVATE-BASIS-42` 哨兵断言私有依据不进投影、跨账户读取隔离；⑨`test_study_verification_migration_export_delete_and_recovery` 覆盖迁移/导出/删除/恢复与逻辑清单；⑩提示词为本项目自研（见 C01）。
- **C01：** 导师提示词沿用 02 建立的 BridGes 合同基线并按本票扩展（唯一待答问题、展示前冻结依据、非作答不判错）；规划器提示词与 JSON Schema 为本项目原创，未移植参考项目代码或提示词。
- **后端验证（conda `agent`）：** 聚焦回归集 22 通过（issue03 行为 13 + issue02 回归 5 + 合同同步 2 + v71 迁移 1 + v70 回归 1）；全量 `tests/chat` 与 `tests/lifecycle`：94 失败 / 1469 通过，94 项失败集合与干净 main 逐条一致（全部在 main 复现），本票无新增失败。
- **前端验证（Node v24.14.0）：** worktree 依赖由与 main 相同 lockfile 的本地补齐；`tsc --noEmit` 干净，`vitest run` 34 文件/260 项通过（含本票新增待答问题展示用例），`next build` 生产构建通过，`next lint` 仅 3 条既有警告。
- **静态检查：** 改动文件 Ruff 通过（修复本票引入的 2 项：import 排序、SIM114；`database.py` 1 项 E501 为 main 既有同文行）；改动文件 mypy 仅 `turn.py` 1 项既有 `str | None` 报错（main 同命令同样存在）；`git diff --check` 通过。
- **剩余限制与后续：** 全仓 pytest 未重跑（以全量 chat/lifecycle 双树对账替代）；规划器依赖已注册的 `qwen_structured_output` 能力，缺失或失败时验证静默降级不写数据（回答不受影响）；用户主动解除等待的界面动作由 04 消费本票语义；正式诊断由 05 消费证据；真实教学效果由 13 独立验收。
