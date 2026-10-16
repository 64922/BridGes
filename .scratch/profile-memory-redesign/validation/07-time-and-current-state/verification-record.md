# 07 — 区分当前状态、历史经历与有效期：验证记录

**分支：** `codex/issue-07-time-and-current-state`（基点 `14cea5f6`，main）。
**环境：** conda `agent`（Python 3.11.15）；真实主模型 `qwen3.7-plus-2026-05-26`
（OS 凭据库 `credential_store`，经本机代理 `http://127.0.0.1:7890`）。
**证据：** 本文 + `baseline-stub-20261016T023003Z.{json,md}` +
`baseline-real-20261016T023022Z.{json,md}` + `run-lock-*.json` +
`evidence/demo-walkthrough-20261016T022737Z.txt`（+`-model-outputs.json`）+
`tools/walk_time_and_current_state_demo.py`（可复跑）。
**可复跑：**

    DIR=.scratch/profile-memory-redesign/validation/07-time-and-current-state
    python scripts/run_issue01_profile_baseline.py --mode both --output $DIR
    python $DIR/tools/walk_time_and_current_state_demo.py \
        --out $DIR/evidence/demo-walkthrough-<stamp>.txt [--proxy http://127.0.0.1:7890]

（真实模式需要凭据库中的 Qwen Key。走查约 6 分钟，运行库在 `.tmp/issue07-demo-*`，
脚本只录制聊天载荷片段，绝不记录凭据。基线两种模式各约 1 分钟。）

## 1. 验收标准对照

| 验收标准（票据） | 证据 |
| --- | --- |
| 相对日期、来源时区和周期安排解释正确，跨日/周重试不漂移 | `test_relative_dates_use_product_timezone_day_boundaries`、`test_new_source_message_reanchors_across_days_without_drift`、`test_parse_validity_window_accepts_explicit_timezone`、`test_weekly_schedule_parses_without_expiry`、`test_recurrence_parser_covers_daily_weekend_and_lists`；走查 [1]（「下周」= 本地 10-19~10-25、窗口与按锚重解析一致、周期条目无期限） |
| 完成、暂停、恢复只操作明确定位的目标，歧义不批量修改 | `test_ambiguous_lifecycle_signal_leaves_every_goal_untouched`、`test_exact_goal_target_wins_over_containment`；走查 [2] 暂停只命中唯一目标、[5] 两条「考研」目标原样（真实聊天管道 + 独立账户） |
| 「继续」不自动延长旧期限，续期需新的明确依据 | `test_resume_keeps_old_deadline_and_expired_goal_stays_out`、`test_resume_with_new_explicit_date_renews_window`；走查 [2] 恢复后窗口保持 `2026-10-25 15:59:59+00:00`（UTC） |
| 过期约束不进入当前计划，历史事实仍可按适当用途读取 | `test_expired_experience_enters_review_purpose_only`、`test_completed_goal_is_history_not_current_plan`、`test_correction_never_reads_as_history_state_change_does`、`tests/chat/test_issue07_review_history_injection.py`；走查 [4]（复盘载荷携带「既往经历（回顾用途）」，用而不改条目） |
| 未使用不自动过期，召回频率不增加事实可信度，不设统一遗忘 TTL | `test_repeated_use_does_not_change_facts_or_confidence`（多次采用后 version/confidence/窗口不变）；周期条目 `validity_status=unbounded`（走查 [1]/[3]） |
| 页面、存储和采用一致；生命周期和时间数据支持迁移、导出、账户删除及恢复 | `test_api_evidence_returns_recurrence_for_page_consistency`、`tests/storage/test_schema_v83_profile_recurrence.py`（2 项）、`tests/lifecycle/test_issue07_time_and_current_state_data.py`（导出/删除隔离/备份恢复 2 项）；前端 `AtomicProfileCenter.test.tsx`「每周三晚上固定进行…不设统一过期时间」；走查 [3] 证据投影与页面一致 |

## 2. 真实模型走查（最终代码，20261016）

1. 时间锚：`我下周要参加英语六级考试。` → 真实抽取落库文本保留「下周」，
   窗口（本地）`2026-10-19 00:00 → 2026-10-25 23:59:59`（Asia/Shanghai），
   与按存储锚重解析一致——解析一次、不随使用重解释。
2. 周期：`我每周三晚上去实验室做实验。` → `recurrence=weekly/(周三)/晚上/
   每周三晚上`，无期限；证据投影 `recurrence_phrase=每周三晚上、
   validity_status=unbounded`。
3. 生命周期（真实聊天管道，`source=local_rule succeeded_lifecycle_signal`）：
   暂停只命中「我计划下周考六级」（goal_state=paused），计划用途切片排除
   理由「目标已暂停，不再作为当前目标使用」；恢复后 goal_state=active 且
   窗口保持原值（不续期）。
4. 回顾：独立白纸账户种一条 30 天前的过期经历；复盘问题在新会话提出，
   运行记录采用切片 `task_kind=review`、`('我下周有英语六级考试',
   '既往经历（回顾用途）')`，模型载荷实含
   `[1]（既往经历（回顾用途））我下周有英语六级考试。`，助手回答直接引用
   该事实；使用前后条目 v1/v1、正文/期限/置信度不变。
5. 歧义守卫：独立账户种「我计划考研英语」「我计划考研数学」两条，「暂时
   不考研了」经真实管道后两条均 `active`（不做批量修改）。

观察项（模型波动，非机制问题）：更早两轮走查中真实模型曾把时间从句从
落库文本中丢掉（「我在准备英语六级考试，下周要考试」→ 只写「我在准备
英语六级考试」）、或给条目文本加句尾句号；走查脚本对前者改用同一消息的
确定性写入补种子并如实标注，对后者按去标点后的文本匹配。

## 3. 自动化测试

- 新增 `tests/profiles/test_issue07_time_and_current_state.py`（22 个函数，
  memory+sqlite 双子 fixture 共 28 项）：时区日界/跨日重锚/显式时区参数/
  周期解析（每天/每周末/列表）/证据投影/重复采用不变/精确目标优先/歧义
  空操作/恢复不续期/新日期续期/过期经历仅回顾/回顾切片失效门禁/已完成
  目标/纠错不作经历/API 证据。
- 新增 `tests/chat/test_issue07_review_history_injection.py`（2 项，回归）：
  回顾轮载荷注入「既往经历（回顾用途）」；计划轮不读取过期经历。
- 新增 `tests/storage/test_schema_v83_profile_recurrence.py`（2 项）：
  v82→v83 升级加列、旧行 `recurrence_json` 为 NULL 且迁移幂等。
- 新增 `tests/lifecycle/test_issue07_time_and_current_state_data.py`（2 项）：
  时间/周期字段进入导出、账户删除隔离、备份恢复往返一致。
- 更新既有：`tests/profiles/test_issue18_profile_validity_and_revocation.py`
  与 `test_issue18_automatic_lifecycle_and_retry.py`（时区预期改为
  Asia/Shanghai 下「下周」的 UTC 界值，锚注释明确）。
- 聚焦复跑（修复后）：`pytest tests/profiles tests/chat tests/storage
  tests/lifecycle tests/contracts tests/evaluation` → **2283 项，106 failed
  / 2177 passed**；106 项与改动前采集的既有失败集合逐条一致（chat 94 项
  逐名差集为空；evaluation 12 项相同）；profiles/storage/lifecycle/contracts
  0 失败。
- 修复证据（先失败后通过）：回顾切片在发送前被 `is_adopted_slice_current`
  判失效（历史条目按定义「不可常规召回」），导致历史永不进入载荷。
  `tests/chat/test_issue07_review_history_injection.py` 修复前失败、修改
  `is_adopted_slice_current`（回顾历史条目豁免召回资格复核，删除/改动
  仍由版本/状态核对拦下）后通过，并由走查 [4] 在真实管道复证。
- 全量 pytest（改动前、本分支 vs main 同命令）：分支 342 failed / 5648
  passed / 39 skipped / 4 collection errors，全部失败项在 main 同为失败
  （337 项批量复跑 + 5 项逐条复核）；collection error 为 main 既有
  `tests/career_plan` 导入问题。改动后聚焦批次已覆盖本票全部路径。

## 4. 静态检查

- `ruff check`（全部改动文件）：仅 2 条既有问题（`database.py:1671 E501`、
  `test_issue18_automatic_lifecycle_and_retry.py:35 F401`），main 同文件同项；
  其余改动文件与走查脚本 0 问题。
- `mypy`（改动 src 文件）：仅 `chat/turn.py:5391`（main 同行 5390）为既有
  问题；其余报错来自未改动的依赖文件（main 同）。
- `apps/web`：`npm run test:unit` 34 文件 **293 通过**（含周期证据展示）；
  `tsc --noEmit` 0 错误；`next lint` 仅既有 warning；`next build` 成功。

## 5. 基线语料对照（stub=deterministic 与 real，均为修复后复跑）

| 旅程 | 预期 | deterministic | real | 备注 |
| --- | --- | --- | --- | --- |
| t01-name / t01-pet / t01-habit | pass | pass | pass | 原子条目写入 |
| t02-psychology | pass | pass | pass | 无心理推断 |
| t03-time-limit | pass | pass | pass | 时间限定保留 |
| t04-cross-turn | pass（05） | pass | pass | 本轮 real 通过；同日更早一轮 real 为 partial（`preference_captured=False`）属模型抽取波动，与工单 06 记录同源 |
| positive-academic-control | pass | pass | pass | `succeeded_written`、active |
| recall-academic-control | known_failing | partial | partial | D11 召回缺口（与 04/05 相同，均为「与预期一致=True」） |

汇总：deterministic 模型调用 10 次（替身 0 token）；real 10 次、输入 4263 /
输出 709 token，回答耗时样本 [2108, 1375, 1984, 2046, 2811, 2703, 7186,
2077, 2719, 1141] ms。

## 6. 语义与实现说明

- **时区与解释**：`PROFILE_TIMEZONE="Asia/Shanghai"`；`parse_validity_window
  (text, anchor, *, timezone=...)` 把锚转换到产品时区后按本地自然日/周/月
  解释，再转回绝对时刻存储。锚是来源消息时间；重试沿用存储锚，不按执行日
  重解释（跨日/周不漂移）。「七天」等时长型与「下周/下个月」日历型分别处理。
- **周期与期限是两条正交通道**：`AtomicProfileRecurrence(kind, weekdays,
  time_of_day, phrase)` 随条目展示并进入证据投影；周期条件本身无期限，
  不参与过期判断（`validity_status=unbounded`），具体计划依赖时由既有
  「适用条件」提示先核实仍有效。
- **生命周期信号定位**：`_match_goal_targets` 精确相等优先；否则唯一包含
  匹配才生效；多个包含匹配视为歧义返回空（不批量修改，也不当作信号消化，
  按普通事实流程继续）。恢复/完成/暂停共用该定位。
- **回顾用途**：`compile_adopted_slice` 在 `task_kind=REVIEW` 时经
  `_review_history_candidate`（tier 2）采用词面相关的历史：活动但已过期、
  已完成，以及因「状态变化」退休的被替代条目；纠错退休、撤回、暂停、
  未解决冲突、低可靠度一律不进入任何用途。历史条目排在当前条目之后，
  只填剩余最小切片预算（工单 04 整条采用语义）。
- **失效门禁（本轮修复）**：`is_adopted_slice_current` 原先对全部采用条目
  复核召回资格，而回顾采用的历史条目按定义不可常规召回，导致整张切片在
  发送前被判失效、历史永不进入载荷。修复为：`applicable_to` 含
  `review_history` 的条目豁免召回资格复核；删除、版本或状态变化仍由
  `snapshot_versions` 核对拦下（回归测试锁定）。
- **存储与数据生命周期**：迁移 83 为 `profile_items` 增 `recurrence_json`
  （旧行为 NULL，迁移幂等）；SQLite 序列化与导出/删除/备份清单走既有
  `profile_items` 通道，无额外路径。
- **页面**：证据面板展示周期文案「每周三晚上固定进行（长期有效，不设统一
  过期时间）」（`data-testid="atomic-evidence-recurrence"`），与证据投影
  同源；`openapi.json` 与 `packages/contracts/src/generated.ts` 已再生成。

## 7. 剩余限制与边界

- 真实模型抽取有波动（见 §2 观察项）：走查以确定性写入补种子并如实标注，
  机制正确性由自动化测试锁死。
- 回顾历史仅在输入预算够用时注入（tier 2 最后，长对话可能整条裁掉）；
  白纸账户 + 新会话复验证实预算充足时可达模型，超预算时按工单 04 语义
  整条不采用，不影响当前条目注入。
- 走查 [4]/[5] 慢流式下会出现 `status=streaming` 的「等待后重捕」提示，
  等待策略最终捕到回复；不重发消息，不产生垃圾轮次。
- `tests/profiles/test_issue07_time_and_current_state.py` 约 640 行，超过
  全局「单文件 ≤500 行」建议；与仓库既有测试文件规模一致，后续按主题拆分。
- 输入预算、评估相对值口径等其他票的既有问题不在本票范围（`§4` 的 ruff/
  mypy 既有项同）。
