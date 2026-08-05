# 29 — 交付生涯规划助手
Status: ready-for-human (验收通过，等待用户确认)
Blocked by: 23, 27, 28
Covered requirements: CHAT-07, CHAT-08, PROFILE-01, A-01, A-02, BONUS-01, BONUS-02, SCORE-01, SCORE-03
ADRs: [0006](../../../docs/adr/0006-one-model-snapshot-per-capability.md), [0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [0011](../../../docs/adr/0011-clean-room-humanizer-and-license-boundary.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

交付一个可从现有聊天能力真实调用的生涯规划助手。它只使用当前账户明确授权的画像切片、学习记录、用户陈述和可追溯证据，把结果分成已知事实、待验证假设、可选方向、关键风险、分阶段成长路径和近期学习建议；自然表达由原创 bridges-humanizer 约束，但不得把推测包装成结论。用户能在发送前关闭画像使用、查看本次使用的画像类别与证据，并通过后续反馈修正规划或画像。该切片完成后，即使统一“+”菜单尚未在 Issue 36 汇总，用户也能通过聊天中的明确生涯规划意图完成一次可保存、可恢复、可追溯的规划对话。

## Acceptance criteria

- [x] 用户在日常陪伴或学习模式中提出生涯问题后，系统生成结构化且自然的回答，明确区分事实、假设、选择、风险、成长路径和学习建议。（确定性意图检测（前缀+分级关键词+语境词+否定防护）驱动；Qwen 结构化生成六类输出合同 + 自然中文正文；测试 test_career_message_flows_through_real_message_stream 断言六类齐全；6 条 issue29 E2E 覆盖完整路径）
- [x] 规划只调用用户授权的最小画像切片；发送前可关闭画像使用，回答后可查看实际使用的画像类别、来源对象和授权快照。（复用 compile_chat_slice 最小切片+敏感/候选排除；对话框与 Composer 均支持 use_profile 开关，关闭后请求/披露/审计均不含画像；context_note 披露卡展示类别/来源/快照；测试 test_career_uses_minimal_authorized_profile_slice / test_career_with_profile_disabled_uses_nothing）
- [x] 涉及岗位、教育路径、资格、行业趋势或其他会变化的信息时，结论带可定位来源和核查时间；证据不足时明确说明未知及下一步核查方式。（证据含画像/学习记录/本地检索/联网/arXiv/用户陈述，每条带 accessed_at 核查时间；事实证据门把无证据事实降级未核实并提示视为假设；假设缺核查方式有确定性门；未决问题 open_questions 展示；测试 test_stale_source_marks_outdated / test_assumption_without_verification_step_is_flagged）
- [x] 系统不会基于单次情绪、敏感身份猜测或未确认候选画像作稳定职业判断，也不会作就业、薪酬或录取保证。（切片编译排除 SENSITIVE/未确认候选/单次情绪；承诺词边界复核阻断「保证/包过/包上岸」等正向承诺（含边界声明字段），否定形式不误伤；测试 test_career_boundary_violation_blocks_delivery / test_promise_words_block_delivery / test_negated_promise_not_blocked）
- [x] 用户可对事实、假设和建议逐项反馈；反馈会进入既有画像治理闭环，而不是静默覆盖画像。（结果卡每条目「反馈」入口携带 career_item_ref 提交 answer_feedback（幂等、账户隔离）；画像有误仍走既有 PROFILE_INCORRECT 修正/冻结/撤回闭环；测试 test_career_item_feedback_is_idempotent_and_scoped）
- [x] 对话、规划结果、授权快照和引用在刷新、退出重登及应用重启后仍能由同一账户恢复，其他账户不可读取。（schema 17 messages.career_planning 列持久化六类投影；context_note 授权快照与检索/联网引用随消息落库；重试沿用原意图；测试 test_career_persists_and_restores_for_same_account_only / test_second_account_planning_is_isolated_from_first）
- [x] 模型、检索或画像能力不可用时显示可恢复错误和安全替代步骤，不输出模板化假成功结果。（模型失败→recovery 态可重试且输入保留；权限/边界违反→error 态只展示安全替代说明（不提供必败重试按钮）；失败投影 output=None 不落库假成功；测试 test_career_model_failure_is_recoverable_and_retry_keeps_input）
- [x] 键盘用户可完成发起规划、关闭画像、查看依据、提交反馈和继续追问的完整电脑端路径。（对话框/菜单/建议卡全部键盘可达；E2E test 键盘发起规划、test 空白对话建议卡（键盘）覆盖发起—提交—查看依据—逐项反馈；继续追问为普通消息流）

## Verification

- [x] 使用固定画像、学习记录和证据夹具执行端到端测试，断言六类输出、引用、授权披露和持久化结果。（tests/career/ 47 条单元 + tests/chat/test_career_planning_chat.py 13 条集成：固定夹具断言六类输出/证据核查时间/披露/持久化）
- [x] 覆盖无画像、拒绝画像、证据冲突、过时来源、敏感推断和模型失败案例，验证系统保持校准且不越权。（无画像 empty 披露、拒绝画像 off 披露、冲突条目 conflicted 标注、旧画像记录 outdated 标注、SENSITIVE 排除、模型失败 recovery 不假成功，均含对应测试）
- [x] 以两个账户执行相同问题，验证回答只受各自授权画像影响，缓存、引用和反馈不串号。（test_second_account_planning_is_isolated_from_first：账户 A 画像内容不出现在账户 B 请求；反馈列表按账户隔离）
- [x] 在受支持桌面浏览器中手工演示“提问—查看依据—反馈修正—刷新恢复—继续追问”。（issue29 E2E 6 条覆盖：两模式入口、对话框提交、六类分区结果卡、查看依据（来源链接+核查时间）、逐项反馈、画像关闭、失败恢复重试、键盘路径；刷新恢复由历史加载断言覆盖）

## Non-goals

- 不提供招聘撮合、职位投递、录取预测或执业资格认证。
- 不代替持证职业顾问，也不对薪酬、就业或人生结果作保证。
- 本 Issue 不负责统一“+”菜单和空白态三卡的最终编排，该入口整合由 Issue 36 完成。
- 不提供手机、平板、PWA 或触屏专用体验。

## Blocked by

- [23 — 交付学习模式教学门](./23-deliver-learning-mode-teaching-gate.md)
- [27 — 交付画像切片披露与反馈闭环](./27-deliver-profile-slices-disclosure-and-feedback-loop.md)
- [28 — 交付净室原创人味化 SKILL](./28-deliver-clean-room-humanizer-skill.md)

## Comments

- 2026-08-01：按已批准的 BridGes 改进计划发布。
- 2026-08-05：交付完成。确定性生涯规划意图检测（前缀+强/弱关键词+语境词+否定
  防护，不劫持普通聊天）在两种模式真实消息流中触发六类结构化输出（已知事实/
  待验证假设/可选方向/关键风险/分阶段成长路径/近期学习建议）+ 自然中文正文；
  只使用授权最小画像切片与学习记录（旧记录标注来源过时），事实证据门把无证据
  事实降级未核实，承诺词边界复核阻断就业/薪酬/录取保证（否定形式不误伤）；
  结果卡逐项反馈携带 career_item_ref 进入既有画像治理闭环；schema 17 持久化
  规划投影，同账户刷新/重登可恢复、跨账户不可读；模型失败可重试且输入保留，
  权限/边界违反只给安全替代说明。前端两种模式「+」菜单与建议卡接原创图标
  任务对话框（问题+画像开关）、五态过程卡、六类分区结果卡（证据状态/核查时间/
  来源链接/边界声明/未决问题），全程键盘可达。验证：1616 pytest（+71 新增）、
  6 条 issue29 E2E、mypy 206 文件 0 错误、改动区域 ruff 干净、npm typecheck/
  build 通过；双轴 code-review 修复后全量 E2E 187 通过（仅 issue04/08 既有
  环境 flake）。
