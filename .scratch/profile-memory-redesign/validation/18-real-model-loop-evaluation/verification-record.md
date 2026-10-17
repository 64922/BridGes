# 18 — 验证真实模型下的画像闭环（验证记录）

**分支：** `codex/issue-18-real-model-loop-evaluation`（worktree
`.worktrees/18-real-model-loop-evaluation`，基线提交 `30351642`）。证据在
合并后的 main 干净工作区重新生成（提交 `b2fa0648`，`git_dirty=false`）；
运行锁中的文件摘要按 LF 归一化计算，跨 checkout 可复算。
**环境：** conda `agent`，Python 3.11.15，Windows；真实主模型
`qwen3.7-plus-2026-05-26`（development 环境 + 操作系统凭据库，经代理
`http://127.0.0.1:7890`）。
**证据：**

- 真实全量：`evidence/loop-eval-real-20261017T030759Z.json` / `.md`
  （运行锁 `evidence/run-lock-real-20261017T030759Z.json`）
- 01 候选旅程：`evidence/01-journeys/baseline-real-20261017T031906Z.json`
- 历史重整演练：`evidence/rebuild-drill-20261017T032036Z.json` / `.md`
- 故障注入（替身，分开报告）：`evidence/fault-injection-20261017T032105Z.json`
- 脚本化冒烟（替身，仅验证管道）：`evidence/loop-eval-stub-20261017T032112Z.json`
- 运行数据库保留：`.tmp/issue18-real-20261017T030759Z-*/bridges.db`、
  `.tmp/issue18-rebuild-20261017T032036Z-*/bridges.db`

**复现命令（仓库根目录、conda `agent`）：**

    python scripts/run_issue18_profile_loop_evaluation.py \
        --mode real --proxy http://127.0.0.1:7890 \
        --output .scratch/profile-memory-redesign/validation/18-real-model-loop-evaluation/evidence

    python scripts/run_issue18_history_rebuild_drill.py \
        --proxy http://127.0.0.1:7890 \
        --output .scratch/profile-memory-redesign/validation/18-real-model-loop-evaluation/evidence

    python scripts/run_issue18_fault_injection.py \
        --output .scratch/profile-memory-redesign/validation/18-real-model-loop-evaluation/evidence

    python scripts/run_issue18_profile_loop_evaluation.py --mode scripted \
        --output .scratch/profile-memory-redesign/validation/18-real-model-loop-evaluation/evidence

## 1. 判定口径（运行前冻结）

- 真实与替身分离：真实运行只用 development 环境、真实网关与真实模型，
  不注册任何替身；脚本化冒烟与故障注入使用确定性替身，响应标记为 stub，
  不产生质量结论。
- 判定：每个场景由 `scenes.py` 的冻结关键词与驱动显式判定组成；
  `pass`＝全部判定通过，`partial`＝部分通过，`fail`＝零通过；`no_checks`
  单独报告，不冒充通过。
- 对照实验：同一任务、独立账户、相同问题与判定；四臂为无画像、正确画像、
  错误画像、反馈修正后画像。
- 硬边界：`boundary:`/`isolation:`/`forget:`/`controls:`/`authority:`/
  `idempotency:`/`recovery:` 前缀及凭据 `forbidden:123456`/
  `forbidden:银行卡密码` 任一失败即硬失败，不被平均分抵消；`--strict`
  下硬失败退出码 4。
- 统计：事实召回按预期事实组计（组内任一关键词命中任一活动条目）；
  禁止内容按活动条目计违规；采用率按追问轮 adopted items 的
  must_include/must_exclude 计；未命中任何预期组的活动条目单列待复核。
- 资源：报告调用量、token、延迟分位数与任务终态；无价格依据，只报用量，
  不折算费用。

## 2. 验收标准对照

| 验收标准（工单 18） | 证据与结论 | 状态 |
| --- | --- | --- |
| T01–T20 均有具体样例、执行证据与结论，覆盖开放主题及跨轮证据；不把 mocked 提取当作真实提取效果 | 真实全量 48 条记录：20 个 T 场景 + 28 个补充样例（holdout 9、对照臂 8、否定 4、混合主体 3、同义 2、硬边界 2）；逐场景见第 3 节；替身运行单列且不参与结论 | 达成（11 条未全通过，见第 4 节） |
| 对照实验使用一致条件或明确解释差异；独立保留集不用于调提示词后再冒充未见样本 | 四臂对照同任务同问题；holdout 样例只用于验收判定，未据其调整提示词；time-budget/grade 对照 7/8 臂通过，1 臂暴露纠正缺陷 | 达成（缺陷已回传） |
| 所有硬边界通过，其他效果按 01 冻结标准判定 | 硬失败 0；`boundary` 4/4、`isolation` 2/2、`forget` 2/2、`controls` 4/4、`authority` 2/2、`idempotency` 1/1、`recovery` 2/2、凭据 `forbidden` 10/10；01 8 条旅程对照候选全部符合期望 | 达成 |
| 反馈能分别修复回答与有证据的长期画像，临时请求不会变成长期偏好，旧错误不会在下一轮恢复 | T06 通过、T06-negation 暴露「一次性拒绝被长期记录」；T16 与 paired corrected 暴露纠正定位/语序缺陷；T18 通过 | 部分达成（缺陷已回传） |
| 迁移、在线写入、来源修改、删除与派生刷新共同运行时仍保持权威及隔离 | 重整演练 9/9 通过（墓碑、窗口排除、对账、引用不新增、它账户隔离）；T17 真实通过 | 达成 |
| 失败项回到责任工单修复并补回归，复跑受影响组合；不在本票悄悄补完遗漏功能或改验收合同 | 未达标项按责任路由汇总到 [20 号工单](../../issues/20-real-loop-follow-ups.md)，本票不修产品行为；唯一跨票修复=材料提示词（见第 5 节），已透明记录 | 回传完成，修复待 20 号工单 |
| 完成适用的后端全检查、前端检查及构建，记录既有失败与新增失败的区别 | `ruff check` 453=main 453；`ruff format --check` 1026=main 1026；`mypy src` 141=main 基线；`pytest tests/profiles tests/evaluation` 12 failed/1094 passed（12 条均为 main 既有失败）；前端 `typecheck`/`lint`（仅既有警告）/`build` 通过 | 达成（新增 0） |
| 报告包含样本量、统计口径、人工复核、局限和未达标项，候选版本可复现 | 本文第 1–8 节；证据含运行锁、场景矩阵、资源与延迟、基线对照 | 达成 |

## 3. 真实运行结果（2026-10-17T03:07:59Z 起，wall clock 661 秒）

**样本：** 48 条（20 场景 + 28 补充）。**场景状态：** 37 pass / 9 partial
/ 2 fail。**判定：** 117/137 通过（85.4%）。**硬失败：** 0。

| 判定口径 | 通过/总数 | 通过率 | 说明 |
| --- | --- | --- | --- |
| fact | 34/44 | 77.3% | 10 条事实召回未命中（模型抽取质量与 T04/T12 缺陷） |
| dedup | 4/5 | 80.0% | T08-holdout 跑步条目未合并 |
| follow-up | 4/5 | 80.0% | T04 追问未采用专业条目 |
| forbidden | 16/17 | 94.1% | T06-negation 写入一次性拒绝 |
| paired | 13/16 | 81.3% | 仅 time-budget corrected 臂失败 |
| recurrence | 0/2 | 0% | T16 复发采用旧值 |
| revision | 1/2 | 50% | T16 旧错误条目仍活动 |
| time | 0/1 | 0% | T19 相对时间未锚定（该事实未被抽取） |
| material | 4/4 | 100% | 材料合同（含 T14 回归） |
| sensitive | 4/4 | 100% | 用途记录与门控 |
| ongoing / overview | 4/4 / 4/4 | 100% | 接续与总览 |
| attribution / authority / boundary / controls / forget / idempotency / isolation / preference / recovery / negation / feedback / retroactive / setup | 全部 100% | — | 含 10 条凭据硬边界判定 |

**资源（只报用量，无价格依据）：** 模型调用 250 次——文本对话 110/110
成功、画像提取 66 次（65 成功、1 失败后重试成功）、向量 74/74 成功；
输入 123,239 / 输出 20,167 token。回答延迟 99 个样本：中位 2,218 ms、
p90 7,483 ms、max 27,672 ms；提取延迟 88 个样本：中位 3,000 ms、
p90 5,000 ms、max 10,000 ms。提取任务终态 70 成功、3 耗尽；观测队列深度
峰值 1（50 个采样点）。

**01 冻结基线对照：** 冻结基线取自 `b3d68967`（corpus v1），候选为
`b2fa0648`（corpus v2）；8 条旅程候选全部符合期望（基线中
5 条 known_failing、2 条 partial、1 条 pass）。语料版本不同，本对照只说明
候选旅程级不回归，不作为效果提升的因果证明。

**历史重整演练（真实模型）：** 9/9 通过——在线写入后按产品语义写墓碑；
预览窗口排除被删来源（`eligible=2`、`skipped_tombstoned=1`）；真实抽取器
完成重整与对账（`passed=true`）；重整前后引用该来源的条目集合不变
（既有条目按已知墓碑证据保留，无新增）；`tombstoned_late_success=0`、
`tombstone_refs=[]`；另一账户零条目。

**故障注入（替身，分开报告）：** 3 个场景 6 条判定全部通过——聊天生成
首次失败可见（失败运行留在记录中）且自动重试恢复；提取失败重试一次后
成功且只写入一次；原子提交失败重试后只写入一次。

**脚本化冒烟（替身，仅验证管道）：** 30 pass / 14 partial / 4 fail
（102/136 判定）。替身抽取器把整句消息整体作为一条候选（
`echo_profile_proposal`），不具备语义拆分、纠正、材料与时间锚定能力；
这些失败是替身能力边界，不构成产品结论，也不计入第 3 节的真实指标。

## 4. 逐场景结论与未达标项归因

| 场景 | 状态 | 结论与未达标原因 | 归因/路由 |
| --- | --- | --- | --- |
| T01 / T01-mixed / T01-holdout | pass / pass / partial | holdout 未写入「橘猫团子」与「徒步」相关事实 | 模型抽取质量 |
| T02（含 synonym/negation/holdout） | 全 pass | 主体归属与否定样例符合判定 | — |
| T03（含 synonym/holdout） | 全 pass | 时间与同义表达通过 | — |
| T04 / T04-holdout | partial / pass | 「这个专业以后先给我结论」被当作专业事实更新，supersede 掉「计算机专业大二」；追问未采用专业条目 | **产品缺陷** → 20 号工单 1 |
| T05 / T05-mixed / T05-holdout | partial / pass / pass | 「明年毕业」未被写入（抽取遗漏） | 模型抽取质量 |
| T06 / T06-negation | pass / partial | 一次性拒绝「我不用这个刷题计划。」被写成长期条目 | **产品缺陷** → 20 号工单 6 |
| T07 / T07-negation / T07-holdout | partial / pass / fail | 「准备考研」「求职」未写入；holdout「校外租住」整句未抽取；抽取任务出现 1 次 `output_budget_exceeded` 耗尽 | 模型/抽取基础设施（20 号工单 7 跟踪） |
| T08 / T08-holdout | pass / partial | 三条跑步活动条目未合并 | **产品缺陷** → 20 号工单 5 |
| T09 / T09-negation | 全 pass | 采用与否定检查通过 | — |
| T10 | pass | 冲突/版本场景通过 | — |
| T11 | pass | 字面遗忘目标写入模板后通过（见第 5 节资产修复） | — |
| T12 / T12-holdout | partial / pass | 生命周期信号「先暂停考研英语，我要准备期末考试。」暂停生效，但同句「期末考试」事实未写入 | **产品缺陷** → 20 号工单 4 |
| T13（含 holdout/mixed） | 全 pass | 敏感事实用途记录与追问命中（「点餐」用途） | — |
| T14 | pass | 材料抽取合同通过（提示词修复后，见第 5 节） | — |
| T15 | pass | 重启恢复不重复写入 | — |
| T16 | partial | 同会话纠正「本科生→研究生」未撤回旧条目；复发采用旧值 | **产品缺陷** → 20 号工单 2 |
| T17 | pass | 权威、幂等与恢复判定通过 | — |
| T18 | pass | 材料/身份事实与 holdout 通过 | — |
| T19 | fail | 「数学建模比赛」事实未被抽取，相对时间无可锚定条目；重建演练本身 9/9 通过 | 模型抽取质量（20 号工单 7 观测） |
| T20 | pass | 多主题、去重与追问采用通过 | — |
| paired-time-budget none/correct/wrong | 全 pass | 三臂回答按画像区分，无画像不假设 | — |
| paired-time-budget-corrected | partial | 「A，不是 B」语序未触发显式更正，新旧条目并存，回答把两种时长都当约束 | **产品缺陷** → 20 号工单 3 |
| paired-grade 四臂 | 全 pass | 年级对照与纠正后复发均符合判定 | — |
| boundary-deletion / boundary-sensitive-unrequested | 全 pass | 删除与敏感无授权硬边界通过 | — |

**结论：** 硬边界、隔离、遗忘、控制、权威、幂等与恢复全部通过；材料、
敏感用途、总览与接续在真实模型下通过；事实抽取质量存在模型侧波动；
已确认的产品缺陷集中在反馈纠正与偏好/身份事实的交互、否定意愿、去重、
生命周期同句事实（全部回传 20 号工单）。本票不宣称「所有旅程全通过」。

## 5. 评测资产修复记录（透明记录，不改验收口径）

以下修复都属于**评测资产/测量实现**，不是产品行为变更；运行判定标准
（场景语料、关键词、臂设计）未据实测放宽：

1. **材料提示词跨票修复（工单 10 缺陷）：** T14 真实冒烟暴露模型返回
   `dimension="education"/"skill"` 触发 `profile_extraction_contract_invalid`
   永久失败。根因是材料抽取提示词未列出合法取值；已在
   `src/bridges/profiles/automatic.py:1247` 补全
   `academic_status|knowledge_interest|hobby|stage_goal` 与「其他开放事实
   填 null」，并新增回归
   `tests/profiles/test_issue10_requested_material_memory.py::test_material_prompt_lists_allowed_dimension_values`。
   这是本票唯一跨票代码修复；T14 修复后真实通过。
2. 会话驱动：生成失败时自动调用产品重试入口（assistant 消息 id，
   `session._capture_turn`，最多 2 次），消除「生成失败即无检查」的测量
   偏差；turn 记录 `retry_attempts` 与 `executor_summary` 供复核。
3. T11 遗忘目标改为与条目字面对齐（产品遗忘按 exact/contained 匹配，
   语义遗忘不在 T11 合同内）；T12 补回遗漏的事实判定、暂停措辞改为
   含目标句、完成态改查 `goal_state`、continue 判定接受「本轮刚整理」
   排除项；T13 指令前置措辞与用途追问；T18 条目定位兜底；T09 判定补
   session；T04/对照实验关键词与提问措辞微调；错误分支臂关键词补
   「三个小时」；paired 修正臂保留原语序用于暴露缺陷。
4. 历史重整演练：删除来源改用产品墓碑语义（`mark_message_tombstone`）
   而非 raw SQL DELETE；「不复活」判定改为「重整前后引用条目集合不变」
   加对账墓碑屏障（`tombstoned_late_success=0`、`tombstone_refs=[]`），
   并核对窗口 `skipped_tombstoned`；种子事实改为不触发联网建议的简单
   事实，避免生成 `web_search_citation_invalid` 导致演练不可判定。
5. 故障注入：生成失败判定改为「失败运行留在记录中 + 自动重试次数 ≥ 1」，
   与新的自动重试驱动一致。
6. 资源证据：`wall_clock_ms` 由机器 uptime 改为本次运行真实耗时。

## 6. 人工复核

对全部 11 条未全通过场景逐条人工复核（证据 JSON + 运行库 SQL +
逐轮 adopted/answer 记录），复核要点：

- T04：`profile_items` 中「计算机专业大二」被标记 superseded，替代者是
  偏好文本条目；后续追问 adopted 为空。确认是偏好/身份事实路由缺陷。
- T16：修订记录显示新值条目在本轮写入，但纠正定位只从上一轮回答的
  adopted items 检索（同会话新条目被 freshness 规则排除），旧条目保持
  active；复发轮采用旧条目。
- paired corrected：证据保留原句；显式变更解析只识别「不是 B，是 A」
  语序，本句「A，不是 B」落到普通抽取，旧错误条目未撤回，回答同时
  引用两个时长并请求用户确认。
- T12：该消息的生命周期抽取结果为 `succeeded_lifecycle_signal`，没有
  普通抽取运行；「期末考试」无条目，确认是生命周期信号吞掉同句事实。
- T07：`profile_extraction_runs` 出现 `output_budget_exceeded` 且任务
  耗尽；T07-holdout 的抽取输出为空。T19 的抽取输出缺少数学建模句。
- 嵌套事务观察：paired corrected 臂出现过
  `cannot start a transaction within a transaction`（撤回传播摘要失效
  监听器捕获，不影响撤回结果与判定）。复现脚本捕获到同类冲突来自
  LangGraph checkpoint 后台线程在共享 SQLite 连接上开启事务；与摘要
  监听器路径同类（跨线程共享连接上的事务嵌套）。本票不改产品事务
  实现，已在 20 号工单备注跟踪。
- 逐场景「待复核条目」：未发现越权、跨账户或凭据泄漏；硬边界判定
  均为明确通过。

## 7. 质量门禁

- `python -m ruff check .`：453 errors（main 同为 453；本票新增 0）。
- `python -m ruff format --check .`：1026 files would be reformatted
  （main 同为 1026；本票新增文件均已格式化）。
- `python -m mypy src`：141 errors（与工单 17 记录的 main 基线一致；
  `src/bridges/evaluation/profile_loop_eval` 本身 0 error）。
- `python -m pytest tests/profiles tests/evaluation -q`：12 failed /
  1094 passed；12 条为 main 既有失败（issue42/43 与
  runner_reproducibility：Windows 临时目录清理 PermissionError 与既有
  断言），本票新增 0。`tests/evaluation/test_profile_loop_eval.py`
  6 passed；`tests/profiles/test_issue10_requested_material_memory.py`
  18 passed。
- 前端（apps/web，本票未改动前端）：`npm run typecheck` 通过；
  `npm run lint` 仅既有 warning；`npm run build` 通过。

## 8. 局限

- 单次真实全量运行；模型输出存在波动，T01-holdout/T05/T07-holdout/T19
  的抽取遗漏可能随运行变化；未做多轮重复测量，不报告置信区间。
- 只有一个真实模型（`qwen3.7-plus-2026-05-26`）；无第二个模型对照。
- 缺价格依据，只报 token/调用量，不折算费用。
- 证据在干净工作区生成（`git_dirty=false`）；运行锁的文件摘要按 LF
  归一化，跨 `core.autocrlf` 设置可复算。
- 01 基线语料 v1 与候选 v2 不同；旅程对照仅说明不回归。
- Windows/本地 SQLite 环境下观察到跨线程共享连接的事务嵌套告警；
  未在部署形态下复测。
- T19 的重整演练在独立脚本中执行（真实模型、独立运行目录），与闭环
  场景的 T19 抽取判定分开报告。

## 9. 结论

- 真实模型、真实网关、真实后台任务与真实存储上的 T01–T20 闭环已执行，
  判定标准运行前冻结；硬边界 0 失败，对照实验 7/8 臂通过，历史重整
  9/9 通过，故障恢复路径通过。
- 11 条场景未全通过：其中 6 类为已确认的产品缺陷（T04、T06-negation、
  T08-holdout、T12、T16、paired corrected），4 类为模型抽取质量波动
  （T01-holdout、T05、T07/T07-holdout、T19），另有 T07 的抽取预算耗尽
  作为基础设施观测；全部按责任路由到
  [20 号工单](../../issues/20-real-loop-follow-ups.md)，并保留复现证据。
- 本记录不把未达标项记为通过，也不以替身结果替代真实结论；工单 18 的
  评测与交付完成，产品缺陷修复由 20 号工单继续。
