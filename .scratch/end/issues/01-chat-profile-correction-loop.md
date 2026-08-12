# Issue 01：闭合聊天中的画像纠正回路

Status: resolved

Type: task

Priority: P0

User stories: US-01

## What to build

交付"用户反馈→修正画像"的确定性闭环：用户在聊天中明确表达纠正意图（如"我改主意了，把这条画像内容修改成我喜欢学习卷积神经网络相关知识""把我的关注点换成 CNN""不是 Transformer 而是卷积神经网络"）时，系统在现有自动画像抽取管道内识别纠正意图、解析目标维度与新值，对该维度现有的 active 画像记录执行**原地 UPDATE**（复用 `modify_record` 语义：`content` 更新、`correction_count+1`、`change_note` 置位、`updated_at` 刷新），并在模型生成回复**之前**把真实写入结果注入上下文——写入成功则回复可自然确认；意图未识别、目标不明确或被保护规则拒绝时，回复不得声称已修改，并说明可在"用户画像"页手动修改。

本切片必须覆盖四个维度（学业情况、感兴趣的知识、兴趣爱好、阶段目标），其中"感兴趣的知识"是首要场景。它不引入模型工具调用，不改变画像页手动修改/撤回/删除的现有行为。

## 已验证复现与根因摘要

- 复现输入："我改主意了，把这条画像内容修改成我喜欢学习卷积神经网络相关知识"。实测 `ProfileSignalClassifier`（`src/bridges/profiles/signals.py:105-155`）判为 `no_signal`，整条消息被 `preprocess_message`（`src/bridges/profiles/automatic.py:1222`）静默跳过，画像零写入。原因：`_EXPLICIT_PROFILE`（`signals.py:74-78`）要求"我+喜欢/想/学…"位于句首或紧跟标点，该句中"我喜欢"前是"成"字，"改主意"不在动词白名单。
- 聊天模型请求载荷由 `assemble_payload`（`src/bridges/chat/turn.py:1434-1494`）组装，只有 messages/temperature/max_tokens/global_writing_policy，**无任何工具定义**；"已经帮你把关注点换成 CNN"是模型看到画像切片后的口头迎合，即幻觉式承诺。
- 即使句式被识别为自述，`knowledge_interest` 维度的 CREATE→UPDATE 转换也不存在（`automatic.py:1835-1853` 仅覆盖 `academic_status`/`stage_goal` 与"我更喜欢"句式）——直接说"我喜欢学习 CNN"只会新增记录，旧记录并列保留。
- 截图中卡片"用户纠正后已更新 · 10:09"来自画像页"修改"按钮（PATCH → `modify_record`，`src/bridges/profiles/four_dimensions.py:1045-1089`），该函数即使内容不变也刷新 `updated_at`，与聊天无关。
- 现有测试只锁定 `stage_goal` 原地更新（`tests/profiles/test_automatic_profile_extraction.py::test_later_explicit_stage_goal_updates_in_place`），无任何测试覆盖"聊天中要求修改画像"的场景。

## 非目标

- 不引入模型工具调用/function calling，不在模型载荷中添加 tools 字段。
- 不做确认卡、不新增前端事件卡组件；闭环可见性完全依靠回复措辞与画像页真实数据。
- 不改变画像页手动修改、撤回、删除按钮的现有行为与 API 合同。
- 不处理旧治理模型（`ProfileObservation/Candidate/Assertion`）的纠正语义。
- 不放宽 `correction_count >= 3` 防抖动保护（`four_dimensions.py:981-982`），聊天纠正同样受其约束。
- 不做模糊/隐式兴趣漂移的自动改写（如"最近在看 CNN 论文"不触发纠正），只处理明确纠正意图。

## Acceptance criteria

- [ ] 给定账户已存在 active 的 `knowledge_interest` 记录"Transformer的相关基础知识"，输入"我改主意了，把这条画像内容修改成我喜欢学习卷积神经网络相关知识"后：该记录 `content` 原地更新为 CNN 相关新值，`correction_count+1`，`change_note` 按 `modify_record` 现有档位规则置位，`updated_at` 刷新；画像页刷新后"感兴趣的知识"显示新内容，不再新增并列记录。
- [ ] 同一输入下，助手回复包含对修改成功的确认（措辞不限），且确认内容与实际写入的维度、新值一致；回复中不出现与真实结果矛盾的承诺。
- [ ] 纠正意图识别覆盖常见句式：直接替换（"把这条画像修改成…"）、转向（"我改主意了/我换方向了，现在想学…"）、否定替换（"不是 X，而是 Y"/"我对 X 不感兴趣了，更喜欢 Y"）。每种句式至少一个确定性测试。
- [ ] 四个维度均可被纠正；消息未指明维度时按新值语义归类（与现有抽取器的维度归类规则一致）；同维度存在多条 active 记录时更新最近一条（与 `upsert_automatic_record` 的 update 语义一致）。
- [ ] 目标记录 `correction_count >= 3` 时：不写入，回复说明该条画像已多次纠正、暂不可信，并引导到画像页手动修改或删除；画像数据保持不变。
- [ ] 无法确定维度或新值为空/无法解析时：画像保持不变，回复不得声称已修改，并说明可在画像页手动操作。
- [ ] 普通自述（无纠正意图，如"我喜欢学习卷积神经网络相关知识"）行为不变：仍按现有 CREATE 语义新增记录；无信号消息仍整条跳过。
- [ ] 纠正管道 fail-open：识别或写入抛异常时只记审计，聊天主流程与回复生成不受任何影响（沿袭 `_process_profile_effects` 现有容错语义，`src/bridges/chat/service.py:1155-1209`）。
- [ ] 写入结果注入发生在模型调用之前，注入内容只含脱敏的维度标签与结果状态；若注入"本轮无画像修改"，模型回复不得出现"已修改/已换成/已更新画像"类表述（可用全局写作策略或切片上下文指令实现，端到端断言措辞）。
- [ ] 历史消息、历史画像版本与审计记录不被改写；新规则只影响新消息。
- [ ] 重启服务后纠正结果持久化；同一消息幂等重放不重复累加 `correction_count`。

## Test plan

- 分类器/抽取器单元矩阵：纠正句式→correction 意图（维度+新值解析正确）；普通自述→CREATE 不变；`no_signal`/`forbidden` 消息仍跳过；隐私拦截规则优先于纠正意图。
- 服务层集成测试（生产同构 SQLite）：纠正→原地 UPDATE 的字段级断言（content/correction_count/change_note/updated_at/version 乐观锁）；多记录取最近一条；`correction_count >= 3` 拒绝写入。
- 端到端聊天测试：三种结局各一——写入成功回复确认、保护拒绝回复说明、未识别回复无承诺；断言回复文本不包含与真实结果矛盾的措辞。
- 画像页链路：纠正后 `GET /profiles/four-dimensions` 返回新内容；前端组件按 `change_note`/`updated_at` 渲染（可复用现有 `FourDimensionProfileCenter` 测试模式）。
- 幂等与恢复：同一 `message_id` 重放不重复纠正；SQLite 重启后记录保持。
- 回归：`tests/profiles/` 全量（自动抽取、四维画像、治理、画像中心）与聊天主流程测试不得变红。

## Observability & rollback

- 记录脱敏结果码：纠正意图识别命中/未命中、维度、写入成功/保护拒绝/解析失败、耗时；不记录画像正文、新值原文、用户消息原文或账户标识。
- 为"识别命中但被保护拒绝"提供独立结果码，避免与未识别混淆。
- 纠正意图规则独立版本化（如 `PROFILE_CORRECTION_RULES_VERSION`），出现误判回归时可通过关闭纠正意图分类回滚，回滚后行为退化为现状（跳过该消息），不影响 CREATE 路径与画像页手动修改。
- 回滚不改写已发生的画像版本与审计记录。

## Blocked by

- None

## Answer

- 2026-08-12：在 `codex/issue-01-chat-profile-correction-loop` worktree 完成聊天画像纠正闭环：覆盖四维意图解析、原地 UPDATE、保护阈值、幂等/重试、SQLite 重启持久化，以及模型调用前的维度/状态脱敏约束。
- 验证：`tests/profiles/` 226 项通过；issue 专项与聊天 payload 测试 26 项通过；相关新增代码 mypy 通过。全仓 `mypy src` 仍有 83 个既有错误；聊天基线有 1 个既有项目归属测试因当前接口按产品契约返回 410 而失败。

## Comments

- 2026-08-12：用户选定"确定性纠正管道"方案，否决模型工具调用、确认卡与"管道+事件卡"。回复一致性（写成功才确认、未识别不承诺）为本 Issue 的核心合同。
- 后续讨论、实施证据和验收结果追加于本节。
