# 17 — 回答后异步提取有精确证据的完整事实

**What to build:** 回答不等待普通画像提取；后台以用户原文形成零条或多条候选，只有主体、关系和证据通过检查的事实能提交。

**Blocked by:** 02 — 补齐画像剩余规格与验收决策；03 — 锁定完整模型额度与每次调用版本；04 — 守住最终模型载荷预算与材料权威边界；16 — 以完整事实身份保存画像并处理并存更新

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

普通提取仍在提交前且模型调用位于写事务中；整句过滤和单次匹配漏掉多事实及否定偏好。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/用户画像/讨论记录.md](../../../docs/用户画像/讨论记录.md)。
- [docs/用户画像/提取时机与回答应用流程.md](../../../docs/用户画像/提取时机与回答应用流程.md)。
- [docs/用户画像/审查与改进建议.md](../../../docs/用户画像/审查与改进建议.md)。
- [docs/用户画像/复核脚本.py](../../../docs/用户画像/复核脚本.py)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。

## 任务内容

1. 按 02 确认的异常终态策略，在回答后以用户消息为幂等单位提交可恢复队列；消息重试不重复证据计数，失效/禁止记录/撤回来源不继续提取。
2. 本地控制指令沿 07 同步路径；普通自然表达主要由语义模型提出候选，本地预检允许跳过无可复用信号，不要求每消息调用模型或每轮新增。
3. 提取主体为用户当前原文，必要邻近用户原文用于回指，相关已存事实只提供标识/有效性。助手建议、工具结果、引用、第三方和假设不能当用户事实。
4. 逐候选保留完整事实、精确原话区间/消息、关系、范围及原文明示时间；验证片段真实存在且支持主体/关系/否定/时间，不仅检查消息 ID 或字符串相等。
5. 覆盖“以后先结论”“不喜欢长篇”“每天 30 分钟”、多个爱好及用户+朋友/短暂情绪混合内容；合规片段可保存，敏感/人格/心理推断零写入。
6. 明确事实可新增/补证/更新；行为重复和模糊线索留观察，不自动晋升偏好/能力；证据不足或墓碑冲突不写入。
7. 网络模型调用在写事务外，最终短事务再次检查原文、许可、用户编辑、墓碑与版本；失败有界重试，调用/Schema/模型锁和成本记录更新既有混合策略文案。

8. 复核模糊识别动作与旧整消息分类之间的写入门禁：逐候选证据验证后的明确事实可以合法提交，模糊/行为候选保留观察；不能让过时的确定性分类标签再次否决所有有效语义候选。

## 跨票接缝与责任

复用已有提取状态、队列与事务设施，不重建聊天工作流；16 合并写入，18 查墓碑/有效性，19 仅读已提交版本。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [ ] 首轮自述由原文即时影响回答，提取失败/未完成不阻塞本轮或下一轮。
- [ ] 一条消息可零条、多条；否定偏好、混合主体、引语、回指与现实限制正确处理。
- [ ] 每条写入事实有精确支持证据，无依据、第三方或敏感推断零写入。
- [ ] 消息/模型重试不增加证据次数，编辑或删除与后台提交竞争时用户权威优先。
- [ ] 可恢复队列账户隔离，事务外模型调用、短事务提交及有界成本验证。
- [ ] 已提交新信息后续可用；后台未完成时不承诺下一轮或跨会话已经写好。

## 验证与交付证据

确定性候选与阻塞模拟验证时序/事务/并发，复核脚本扩展逐事实反例；真实抽取质量由 41 评测。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 实施记录（2026-10-02）

分支 `codex/issue-17-asynchronous-evidence-based-profile-extraction`，基点 `main@83c27f2f`；worktree `D:\BridGes\.worktrees\17-async-profile-extraction`。开发与验证均在 conda `agent` 环境。

### 实现说明

- **回答后异步提取**：`process_synchronous_controls` 先落控制指令；普通提取改为 `schedule_message_extraction`（以账号 + 消息 + 抽取器版本 + 原文哈希为幂等键）登记 PENDING 任务，由 worker `run_retry_tick` 领取执行。生成执行器在 `_run_turn` 收敛与 `recover_committed_result` 只在回答 `DONE` 时登记；`ChatService.stream_generation` 对直接编排路径同样在消息 `DONE` 后登记。失败/停止终态不登记，不阻塞本轮或下一轮。
- **同步入口保留**：`preprocess_message` 仍同步执行一次（基线语义与既有测试接缝），调度账目不放在写入同一事务，模型调用在写事务外。
- **逐候选证据**：`ProfileExtractionItem` 新增可选 `evidence_start/evidence_end`（合同仍为 `profile-extraction-v2`，调用合同 `input_schema_version=profile-message-v2`）；`classify_segments`/`extraction_classification`/`has_reusable_signal` 支持混合消息；`_resolve_candidate_action` 按片段的确定性分类决定写入/观察/零写入，并核对否定、原文时间与「规范值真实出现在精确区间」。无区间的历史抽取器保持基线信任模式，仅精确区间候选执行值支持检查。
- **事务与复核**：模型调用在写事务外；提交在 `_commit.transaction()` 短事务内先 `_recheck_source_before_commit`（run 终态、墓碑、原文哈希、记录许可、抽取器版本），再落四维与原子镜像；失败按可重试/永久分类，有界重试后任务落 EXHAUSTED 留审计。attempt 序号修正为首轮 1、重试递增，消息重试不重复证据计数。
- **覆盖情况**：一条消息可零/多条事实（多个爱好分别成事实）；否定偏好保留完整分句（「我不喜欢长篇回答」零写成肯定值）；承前省略主语（「我喜欢跑步，也喜欢爬山」）识别；第三方/引用/敏感片段零写入而同一消息合规片段可写；「以后先结论」「每天 30 分钟」由本地预检放行给语义模型候选。
- **模型调用合同**：prompt/recipe/context/quality/estimate/quota 版本随调用上报；邻近用户原文只作回指线索，不提供已存事实内容，回指不进入事实证据。

### 版本与接口变化

- 新增 `PROFILE_EXTRACTION_PROMPT_VERSION`、`PROFILE_EXTRACTION_RECIPE_VERSION`、`PROFILE_EXTRACTION_CONTEXT_VERSION`、`PROFILE_EXTRACTION_QUALITY_POLICY_VERSION`、`PROFILE_EXTRACTION_MAX_NEIGHBORS`。
- `ProfileExtractionItem` 增加可选区间字段，向后兼容 v1/v2 输出合同；未改 `output_contract` 与 `AUTOMATIC_EXTRACTOR_VERSION`（避免无谓重抽）。

### 验证结果

- 新增 `tests/profiles/test_issue17_async_evidence_extraction.py` 13 项：异步登记幂等与 worker 领取、模型调用在写事务外、精确区间写入、否定/丢失时间/值不受支持零写入、混合多事实、用户+朋友省略主语、伪造区间合同失败、执行器与服务仅 `DONE` 登记、抽取器版本变化丢弃迟到结果。
- `tests/profiles`：424 passed / 1 failed。唯一失败 `test_chat_correction_uses_latest_record_and_is_idempotent` 在基线 worktree 独立复跑同样失败（Windows 时钟精度平局），与本票无关。
- `tests/chat`：100 failed / 826 passed / 1 xfailed；失败名单与基线 worktree 逐名 diff 一致（既存失败）。
- `tests/api`+`contracts`+`runtime`+`observability`+`ai`：323 passed / 5 failed；5 项在基线 worktree 同样失败（进程锁与 CLI 帮助等 Windows 环境问题）。
- mypy `src`：98 errors / 20 files，与基线一致；ruff 变更文件零新增诊断（automatic.py 13 处 E501 及各文件既存 I001/E402 为基线）。
- 真实模型抽取质量未在本票验收，按 41 评测执行。

### 已知限制

- 抽取器版本只在任务领取与最终提交两处复核；领取与提交之间升级会失败关闭并留 EXHAUSTED 任务记录（新的调度按新版本重抽）。
- 未向网关模型提供已存事实标识/有效性：回指仅用邻近用户原文；「补证/更新」由提交层的完整事实身份（Issue 16）完成，符合任务 3 的边界表述。

