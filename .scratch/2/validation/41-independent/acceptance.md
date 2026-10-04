# 工单 41 独立验收（2026-10-04）

结论：评测工单达标，可以合入。该结论针对有效事实、可撤回机制和实际内容收益的评测交付，不表示所有模型回答均正确，也不表示全仓测试通过。

## 版本与环境

- 固定比较点：main `48d12a454bb3f759d8a1ffcdb9a5b5732073501a`；交付 HEAD `3514e1cd78463b80ba176c01054c029568e627f5`。独立读取工单、规范、代码和运行结果，编码代理报告只用于定位。
- 验收修复：`2d152638`；累计预算判据修复：`12c870c0cf600f57d58468a173698b2952f787d5`。
- Windows、conda `agent`；真实生成锁定 `2d152638`、提示词 v4、模型 `qwen3.7-plus-2026-05-26`。Python/依赖版本、实际源码 SHA256 见 `final-real/run-report.json`，不以 HEAD 替代未提交源码。
- 普通沙箱终端启动报 `setup refresh had errors`；受控提升权限终端可用。conda 默认捕获输出遇到 GBK 编码异常，后续用 `PYTHONUTF8=1` 和 `--no-capture-output`。首次测试仍生成有效 XML。
- 仅使用原创合成账户、原文及回答；真实调用凭据取自既有运行期凭据库，不收集个人聊天，密钥不进入文件/日志。正文作为授权评测产物保存，不混同于普通业务日志。新聊天回归用唯一合成私密标记检查 INFO 日志及采用审计不含正文；未声称覆盖全部第三方传输日志。

## Standards（规范符合性）

独立规范轴初审 2 项明确问题，修复后 0 项遗留：

1. 入口无条件报告 GitHub 代理不可达和 DashScope 直连可用，未实际检测，违反 delivery-and-validation 的真实环境证据要求。已删除静态断言。
2. 旧真实报告提交号与被测脏源码不一致。新增实际源码摘要、Python/依赖环境和机器可读运行报告；最终真实生成在已提交版本运行，离线判据复评另锁版本，不改写生成记录。

重复计分/检查点序列化属于非阻断异味；按最小改动原则未扩大重构。两任务的半小时判据是本评测的具体案例规则，不宣称通用自然语言预算验证器。全部本次修改的生产/测试 Python 文件 ruff 通过；未修改的 evaluation/executors.py 有旧未使用导入，不纳入“全仓 lint 通过”说法。

## Spec（需求符合性）

需求轴初审 4 项不足，已修复并补证；最终复审认为评测工单达标。

| 工单任务 | 本次实际证据 |
| --- | --- |
| 1 完整事实、关系、并存、精准变更、来源量表 | 指标不再奖励同维度唯一；完整期望不能被短主题词满足；自动 profile_recorded 不绕开否定/活动状态守卫。新完整关系和删除/否定反例通过，覆盖/套话缺陷注入可检出。 |
| 2 原创纵向场景和复核缺陷回归 | `final-real/deterministic-report.json` 13场景/41检查点全通过；本次 profiles 重跑包含 issue16 的 preference_and_learning_coexist_with_relations_preserved、grade_and_major_coexist、parallel_goals_coexist；issue19 的 complete_tail_condition_survives_slice_compilation；聊天 issue22 表达策略测试。 |
| 3 即时原文、后台未完成、跨会话、开关、异常、版本和撤回 | 新 SQLite 场景实测 pending/commit、幂等和 worker 领取后撤回；新 ChatService 回归先完成回答、后台仍 pending，原文已进模型，新画像尚不注入；另一会话提交前不采用、提交后可用。以下具名版本/异常回归本次重新执行通过。 |
| 4 同模型四条件真实内容 | 最终8份真实回答、6个抽取探针，原始输出和源码锁保留。离线修复判据8/8通过；独立内容盲评正确对无画像2胜0平0负，详见下表。错误画像导致严格证明或8h计划；过时事实被过滤，无旧考试/兴趣回声。 |
| 5 自述、作答、行为、敏感与无依据边界 | 确定性 self_report_vs_answer_evidence、mixed_subject_quote_emotion、LOW镜像场景；issue17 混合敏感片段与模糊观察回归本次通过。真实候选进一步走实际治理提交，精确证据区间及完整事实受检。 |
| 6 真实效果、时序、成本、事务、阈值及选择建议 | 真实生成输入6250 token、输出18855 token；实际模型调用4次抽取+8次聊天，第三方/引用预检零调用。SQLite模型阶段事务状态False/False，14次事务总持有3.337ms、最大1.042ms；登记到提交2.209ms。仅合成内存SQLite机制测量，不含锁等待/COMMIT，不代表磁盘P95。 |
| 7 页面依据反馈、授权与日志 | issue20 evidence_returns_quote_time_scope_validity_and_locator、four_feedback_kinds_are_recorded_and_never_delete_facts、evidence_and_feedback_api_permissions_and_contract 本次通过；AtomicProfileCenter组件13/13通过。真实桌面E2E前端启动超时，未进入页面断言，保留此限制。 |

验收五项标准均有对应本次证据：探针/硬门、并存/替换/近义抑制/恢复、真实四条件内容、时序/版本/成本/事务、样本/不确定性/阈值/失败关闭。没有新增业务持久状态或迁移；生产改变仅为已有抽取提示词合同修正，其余是评测与回归。

本次重新执行的接缝：

- issue17：`test_executor_schedules_only_after_done_terminal`、`test_chat_service_after_turn_schedules_only_for_done_message`、`test_extractor_version_change_before_commit_drops_late_result`。
- issue19：`test_same_turn_snapshot_is_frozen_for_later_nodes`、`test_same_turn_automatic_extraction_waits_for_next_turn`、`test_late_addition_preserves_snapshot_but_deletion_invalidates_it`。
- 聊天 issue22：`test_retry_after_preference_modify_recompiles_policy_and_slice`、`test_retry_after_delete_drops_rules_and_claims_no_profile`、`test_delete_between_compilation_and_send_drops_adopted_rules`。
- 聊天 issue07：关闭采用后无注入且原文保留、重新打开、停止记录后忘掉。新工单41跨会话聊天回归同时捕获日志及审计。

## 真实回答独立复核与失败关闭

需求轴先只读 `final-real/blind-review.md` 固定评分，再读取映射解盲。0错误或负作用，1部分满足，2满足具体检查点；评分不是准确率。

| case | 正确性/结构或时间适配 | 解盲与证据 |
| --- | --- | --- |
| 01 | 1/1 | 贝叶斯/无画像：“准确率99%”未清楚区分检出率与误报率，先公式后例子。 |
| 02 | 2/1 | 计划/无画像：结构完整，每日2–3h；对真实每天30min用户不可行。 |
| 03 | 2/1 | 计划/过时：每日2–3h，没有旧六级事实回声。 |
| 04 | 1/2 | 贝叶斯/过时：先例子，但16%左右未给检出率条件。 |
| 05 | 2/1 | 计划/正确：每日30min，15min看例题+15min计算，14天7h正确；末两天完整卷未明确拆分。 |
| 06 | 2/2 | 贝叶斯/正确：先例子，检出率99%+误报5%，0.0099/0.0594=16.67%，随后公式与用途。 |
| 07 | 2/1 | 贝叶斯/错误：严格证明、无直观例子，对初学者适配退化。 |
| 08 | 2/0 | 计划/错误：每天8h，对30min用户明显负作用。 |

正确对无画像2胜0平0负：解释顺序/数值条件更完整，时间预算与期末核心内容具体改变；不是按条数、套话或文字不同评分。全部输出仍回答原任务，无关爱好未强行类比。

最终真实入口曾非零退出：`study-plan/correct_profile` 被判据把“两周总共7小时”当单日420分钟。该失败正确留痕，并未放行。修复区分累计预算；9项配对回归通过（含累计预算、8小时、同文、上午下午累计超预算）。`rejudge.py` 对**同一批**保存回答离线复评，无新增调用/挑选样本；`rejudged/pairing-report.json` 锁定判据提交 `12c870c0`、原报告SHA256和生成环境，8/8通过。原始 `final-real/` 失败报告不改写，最终结论以离线复评与内容复核为准。

## 验证及 main 基线

- 交付原版本：evaluation+profiles 622通过/5失败，main同范围608通过/同5失败（`focused.xml`、`41-main-baseline.xml`）。
- 修复后含聊天接缝：676测试，672通过/4失败；main全新隔离临时目录656测试，652通过/同4失败（`final-focused.xml`、`41-main-isolated-baseline.xml`、`baseline-comparison.json`）。失败身份完全相同，根因均为旧 EvalEnvironment 向 ChatService 传已退役 image_service 参数，并伴随Windows清理数据库文件占用。先前画像纠正旧覆盖期望随测试顺序波动，本轮两端均通过，不宣称本票修好。
- main首次扩大验证用了共享pytest临时目录，193项setup错误来自既有被占用文件；改用本票全新 basetemp 后消失，原诊断XML保留。没有删除其他任务临时文件。
- 最后仅改累计预算判据，配对9项全部通过；此前完整36项验收回归通过。原全仓5232/182/1E数字来自编码代理，**未作为独立验收证明**；本次按改变范围运行。
- 页面组件13通过；真实页面E2E服务启动120s超时，单独Next服务也停在Starting；未改前端源码，本次自行启动进程已停止，未把启动失败计作页面通过。

复现：`conda run --no-capture-output -n agent python scripts/run_issue41_profile_evaluation.py --real-probes --output-dir <新目录>`；离线同批复评：`conda run --no-capture-output -n agent python .scratch/2/validation/41-independent/rejudge.py`。测试比较用上述相同目录列表，main显式新basetemp避免共享目录污染。

## 阈值、选择与限制

控制/账户/来源/撤回硬门要求全通过，不能被平均收益抵消；完整事实和具体内容反例均须被检出。实际内容以0/1/2与胜平负评审，基线中发现的模糊检测条件及计划细化不足如实保留，不把自动8/8称全部科学正确。两任务单次配对和6条探针不足以估计准确率或普遍收益，未给虚构P95/显著性。

当前证据支持保留确定性预检+既定模型，优先针对数值条件和计划负载补充样本，不据这6条探针断言语义索引永远无必要；今后规模扩大或召回下降后，按同一量表、原文证据、预算与人工评审再比较索引/模型方案。本票未增加索引或模型复杂度。

仍有限制：真实模型会波动；自动时长解析只覆盖本票规则、不能替代内容复核；完整桌面E2E未完成；事务只测合成内存仓库业务持有时长；全仓未独立重跑；4项旧评测底座失败仍在。上述均明确区分于本次修复和新增回归，未扩大修改无关模块。

## 合并推送与清理

验收通过后的实际提交、远端核对和清理状态追加在交付记录中。其他任务34、37分支/工作树和主工作树原有未跟踪验证产物保留。
