# 39 — 盲评有人味表达收益与交流边界

**What to build:** 在任务帮助与分寸不退化的前提下，以真实模型多轮盲评决定新表达策略是否值得采用。

**Blocked by:** 38 — 在正式界面展示真实进度、可信结果与恢复操作

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

套话词表和“根据你的目标”出现次数不能证明自然度；现有 47 通过/1 失败也不是学习真实体验证据。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/人味化/README.md](../../../docs/人味化/README.md)。
- [docs/人味化/讨论记录.md](../../../docs/人味化/讨论记录.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/人味化/审查与改进建议.md](../../../docs/人味化/审查与改进建议.md)。
- [docs/人味化/复核脚本.py](../../../docs/人味化/复核脚本.py)。
- [docs/人味化/复核结果.json](../../../docs/人味化/复核结果.json)。
- [docs/用户画像/提取时机与回答应用流程.md](../../../docs/用户画像/提取时机与回答应用流程.md)。

## 任务内容

1. 复用现有基线/消融与评测运行锁，创建原创覆盖矩阵。40–60 组含连续多轮的场景可作为起步建议，样本规模不是统计充分性承诺；真实数据需明确授权。
2. 同一模型、任务证据和可比输入/输出额度比较现行 global-chat-lightweight-v2、新策略、仅保留事实/任务合同的简洁基线；记录策略/上下文/证据/配置与调用目的。
3. 盲评隐藏身份、随机交换顺序，多轮评审保留前文。分别评是否听懂、帮助、自然度、分寸、连续性；允许平局及均不愿选择。
4. 事实漂移、伪造经历、越明确边界和失败伪装成功独立硬门，不能被温暖感总分抵消；任务未完成或过度主动算失败。
5. 使用前文、明确偏好、条件承接逐项消融，词表只留诊断。覆盖倾诉/混合排查/引语情绪/感谢收尾/纠正/长任务/工具失败与全部正式路径。
6. 真实测首字延迟、总时长、长度、输入输出 token、重试和人味专属调用数；先测基线再设适合部署的门槛。
7. 报告参与人数、场景分布、胜/平/负、拒选、不确定性和限制；样本不足不宣称提升。提示策略可回滚，05/06 确定性缺陷修复不回滚。

## 跨票接缝与责任

只复用现有评测底座和授权/原创场景，不恢复已退役文章人味评测平台；43 消费真实报告和放行条件。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [ ] 三组真实模型配对、多轮盲评及三项消融有可复现运行锁和量表。
- [ ] 帮助/分寸非劣先过门，再判断自然度，不用套话命中替代收益。
- [ ] 事实/状态/边界硬失败单列，全部正式用户可见路径覆盖可查。
- [ ] 性能/调用成本真实记录且人味专属新增调用为零，限额依据测量。
- [ ] 报告平局/拒选/不确定性；不足证据或失败明确不放行，不宣称已提升。

## 验证与交付证据

先验证配对/盲化/计数机制，再用真实模型和人员评审；保留匿名评分与可重放原创样本，不把个人聊天放普通日志。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 实施记录（2026-10-06，待独立验收）

> 下列为编码代理的实施记录，不代替独立证据；自然度收益结论须待人工盲评提交后按预注册策略判定。

### 代码与产物

- 新增 `src/bridges/evaluation/expression_corpus.py`：59 组原创多轮场景，覆盖倾诉/混合排查/引语情绪/感谢收尾/纠正/续接/长任务/工具失败/明确边界/明确偏好与全部正式路径（14 条，含接线证据接缝）；`real_runnable` 标记真实配对子集。
- 新增 `src/bridges/evaluation/expression_policy_arms.py`：三臂统一编译接缝（`current-v4`=`global-chat-lightweight-v4`；`legacy-v2`=079faab6 冻结回放；`concise-baseline`=仅事实/任务合同）；三臂统一 `output_tokens_for_request` 证据额度，保证可比输出预算。
- 新增 `src/bridges/evaluation/legacy_v2_policy.py`：按 079faab6 冻结回放 v2 策略，仅适配 import 路径与现行快照超集类型（两处，非重新实现）。
- 新增 `src/bridges/evaluation/expression_gates.py`：事实漂移/伪造经历/越界/失败伪装成功/任务未完成/过度主动六类独立硬门；任务必要内容按终态回答检查，中途轮次只检查完成状态与非空。
- 新增 `src/bridges/evaluation/expression_review.py`：盲化（隐藏臂身份、按种子随机交换 A/B）、五维量表 `human-expression-scale-v1`（听懂/帮助/自然度/分寸/连续性，含平局与均不愿选择）、提交解析与聚合（Wilson 区间、评审者一致率）、预注册放行策略 `human-expression-release-v1`。
- 新增 `src/bridges/evaluation/human_expression.py`：确定性套件（31 检查点：覆盖矩阵、三臂差异、三项消融、5 组固定文案路径）、真实发送器（每臂独立内存会话、多轮保留前文、采集运行锁/首字延迟/用量）、成本汇总、运行锁与报告。
- 新增 `scripts/run_issue39_human_expression_evaluation.py`：确定性默认 + `--real-probes` 显式真实配对；输出 `.scratch/2/validation/39-human-expression/` 下 report.md、deterministic-report.json、real-report.json、blind-review.md、提交模板、run-lock.json。
- 新增 `tests/evaluation/test_issue39_human_expression.py`：32 项机制测试（无真实模型调用）。

### 接口/合同变化

- 不新增业务持久状态、对外 API 或迁移；全部为评测域新模块，复用生产 ChatService 的 `writing_policy_compiler` 接缝与既有评测运行锁。
- 新增评测版本：套件 `human-expression-blind-evaluation/1.0.0`、量表 `human-expression-scale-v1`、硬门 `human-expression-gates-v1`、放行策略 `human-expression-release-v1`。
- 放行口径：无人工盲评提交、帮助/分寸非劣未过门或候选臂硬失败 → 不放行；平局与拒选计入统计，不以套话命中替代体验收益。

### 环境

- conda `agent`；Windows；固定模型 `qwen3.7-plus-2026-05-26`（qwen_text_chat/1）；运行期凭据取自 OS 凭据库，未写入报告/日志/提交。
- 真实配对：9 场景 × 3 臂 = 51 次聊天调用（约 7–12 分钟）；`--scenarios` 可调，上限 12。

### 验证结果

- 确定性：31/31 检查点通过（覆盖矩阵、三臂版本差异、三项消融、固定文案路径）。
- 机制测试：本票 32 passed；`tests/evaluation` 124 passed（4 项 `test_runner_reproducibility` 为 main 相同的既有 Windows 临时库清理失败）；表达/画像相关 chat 测试 124 passed。
- 静态检查：ruff 通过；`python -m mypy src` 与 main 基线一致（108 项既有错误，本票新增模块 0 项）。
- 真实配对：51 次调用完成；2 个非候选臂回合因输出额度截断记为 error（候选臂 0 个）；候选臂六类硬门 0 失败；人味专属新增调用为 0；首字延迟均值 7.2–9.0s、总延迟均值 8.8–9.5s（以最终报告为准）。盲评 27 项材料与提交模板已生成，材料不含策略/对照/场景身份；无人工提交 → `inconclusive` 不放行。
- 运行锁记录模型、参数、prompt 版本与全部观察锁；报告含 `run_lock`/`run_lock_digest`/`observed_locks_digest`，可重放。

### 限制与后续

- 真实配对为 9 场景小样本，只证明机制与方向；自然度收益须待人工盲评提交后判定，当前不宣称提升。
- 学习/模块/固定文案路径与工具失败场景不在真实配对内，由确定性覆盖矩阵与既有消费者验收记录核对。
- 排查类回答在模型随机性下可能触及 1024 输出上限（生产用户同样会看到额度提示），报告如实记录该回合终态为 error，不用重试掩盖。
- 人工盲评提交需按 `blind-review.md` 量表填写并注入 `--submissions`；评审人数与判定阈值由放行策略预注册。

### 评审修复（2026-10-06，两轴 code-review 后）

- 统计取向：盲评聚合改为按策略臂取向（`arm_x/arm_y`、`candidate_wins/candidate_win_rate` + Wilson），修复原先按随机 A/B 标签统计导致候选胜率随洗牌漂移的问题；放行改读候选臂胜率与 Wilson 下界，新增「标签翻转仍按臂取向」回归。
- 盲化：对照项改用匿名编号（item-001…），材料不再打印对照名/场景标识/策略臂；新增材料无身份泄漏断言。
- 硬门：修复工具成功被误判为失败伪装（SUCCESS 直接通过，SUCCESS/ERROR/PARTIAL 分支重构）并补回归；任务必要内容明确按终态回答检查。
- 成本：`humanization_specific_calls_zero` 仅在候选调用数多于基线时判非零（原实现把更少调用误报），补回归。
- 模块边界：按「单文件 ≤500 行」拆分——语料拆为 `expression_spec` / `expression_scenarios_daily` / `expression_scenarios_paths` / `expression_corpus`；评测拆为 `expression_provenance` / `expression_deterministic` / `expression_real_run` / `human_expression`；量表与提交拆为 `expression_scale` / `expression_submission`；测试拆为机制与盲评两个文件。场景数据逐字段对照旧实现零差异。
- 报告：新增场景分布与「部署参照（简洁基线实测）」块，供发布票按测量设门槛；回滚说明明确本票不修改生产提示（回滚面为零）；`legacy_v2_policy.py` 为工单任务 2 要求的历史 v2 对照，按 079faab6 冻结回放。
- 量表版本 `human-expression-scale-v1 → v2`（锚点改为两两相对判定，消除 0–5 分锚点与 A/B 四选一不一致）。
- 修复后复核：确定性 31/31；本票 35 passed；`tests/evaluation` 127 passed（4 项为 main 相同的既有 Windows 临时库清理失败）；ruff 通过；`python -m mypy src` 108 项既有错误、本票模块 0 项。

## Comments

### 独立验收（2026-10-06）：不通过，保留分支

规范/需求两轴独立审查、逐条验收、修复与验证见
[独立验收记录](../validation/39-independent/acceptance.md)。

修复提交 `75290ae0` 解决评分与回答错绑、重复计数、秘密回显、单项人数、
自然度拒选、人工硬门漏项及实际执行分布口径。定点 54 passed、确定性
31/31；Ruff/Mypy 与固定 main 逐项零新增问题。

用户确认目前没有真人评分。真实三项消融、完整正式路径执行、测量后的
部署限额仍未完成，不能以确定性机制和“不放行”记录替代。状态改为
ready-for-human 表示目前需要实际人员评审，并不表示其余实现已完成。
原实施报告中的通过数、场景分布和限制为历史记录，以独立验收结论为准。

未合入 main、未推送；Issue 分支及工作树保留，其他任务不变。

### 验收证据补齐（2026-10-06）：三项缺口关闭，结论仍不放行

- 真实三项消融：12 次真实调用，`signal_effect` 由真实编译快照证实；
  修复多轮对照按最后一次编译比较的口径缺陷并补回归。
- 正式路径执行：14 条路径收据 + 2 条修复后的长任务真实运行，11+4 个
  模型锁；`study.scope`/`composite` 为如实标注的策略接缝/确定性收据。
- 部署限额：按冻结报告基线实测定义并全部通过（延迟 1.085×、首字 1.250×、
  输出 1.071×、调用增幅 0、人味专属调用 0）。
- 派生 v2 报告仅改 `validation_scope` 并附证据引用；重评分 missing=0，
  证据 blocker 消除，仍 `not_released`：4 项人工硬门失败 + 帮助/分寸
  有效票不足（6/3、7/4 < 10）。详见
  [独立验收记录](../validation/39-independent/acceptance.md)。
- 首次全量重跑暴露评测侧失败遮蔽：有界重试耗尽后把失败结果当成功返回，
  且学习收据未镜像生产失败口径，导致空载荷被误报为数据校验错误；已修
  并补回归（`1c797faa`），随后全量证据重跑通过（消融 12 次调用校验通过、
  14 路径 + 2 长任务校验通过、31 条重试全部记录）。
- 定点回归 73 passed；未修改生产提示/迁移；其他任务资产保留。
- 按用户明确指示，本分支将在保持不放行结论的前提下合并到 main 并推送。

### 合并推送与清理（2026-10-06）

- 先把 main 合入本分支（合并提交 `0dba6586`，无冲突），再将
  main 快进 `ae3c24f6..0dba6586`；合并后在 main 复核工单 39 五个测试
  文件 73 passed。
- 经用户代理推送：`git -c http.proxy=http://127.0.0.1:7890 push origin main`
  （`ae3c24f6..0dba6586 main -> main`）；推送后 `HEAD == origin/main ==
  0dba6586`，远程仅 `refs/heads/main`。
- `tests/evaluation` 在 main 全量复核 247 passed、4 failed 为 main 相同的
  既有 Windows 临时库清理失败（`test_runner_reproducibility.py`，两处同败）。
- 已删除工单 39 工作树与本地分支并 `worktree prune`；`42-baseline-7818c34`
  工作树与 `codex/issue-03-learning-evidence-consistency` 分支为其他任务
  资产，原样保留。结论仍为 `not_released`。

