# 工单 01 测量方法（冻结版，2026-10-15）

本文件冻结画像闭环基线的评测方法与口径。后续票（04、05、09、11、15、18）
复用同一方法与对照；修改本方法必须同时修改语料版本与运行锁并重跑基线。

## 1. 评测对象与入口

- 所有旅程经实际服务入口执行：`POST /auth/register`、`POST /chat/first-turn`、
  `POST /chat/conversations/{id}/messages`、`GET /chat/conversations/{id}`，
  以及直接 SQL 采集画像三表快照。内存探针只用于定位与交叉核对，不单独
  作为入口证明（工单 01 实施范围第 2 条）。
- 每条旅程使用**独立账户**（入口注册），快照与修订链互不污染。
- 采样时间窗：旅程内 `drive_until_idle` 等待生成与后台提取全部空闲，单轮
  上限 `JOURNEY_TURN_TIMEOUT_SECONDS = 600`（`profile_journey_runner.py`）。

## 2. 双模式与标记

| 模式 | 环境 | 模型 | 标记 |
| --- | --- | --- | --- |
| deterministic | `BRIDGES_ENVIRONMENT=test`，InMemory 凭据 | 聊天与网关抽取均为本地替身 | `response_mode=stub`，`injected_models_overridden=true` |
| real | `BRIDGES_ENVIRONMENT=development`，OS 凭据库 | 真实主模型（`qwen3.7-plus-2026-05-26`） | `response_mode=real`，`injected_models_overridden=false` |

- 确定性模式把规则抽取器替换为 **生产接线的网关抽取器 + 录制替身**
  （`install_deterministic_profile_extractor`，`profile_journey_runner.py`），
  用于在真实入口上复现「会话标识/邻近消息」机制；替身响应不冒充真实模型输出。
- 真实模式不注册任何替身；凭据不可用时写 `inconclusive` 并以退出码 2 结束，
  绝不伪造数据（`scripts/run_issue01_profile_baseline.py`）。
- 两个模式的证据文件同名不同标记（`baseline-stub-*.json/md`、
  `baseline-real-*.json/md`），不得混用结论。

## 3. 冻结内容（运行锁）

每次运行写 `run-lock-{mode}-{stamp}.json`，包含：

- 语料：`corpus_version=profile-loop-corpus-v1` 与 `corpus_sha256`
  （语料文本或预期任何变化都会改变摘要，`profile_journeys.py`）；
- 代码：`git_commit`、`evaluation_sha256`（评测模块与脚本集合）、
  `pipeline_snapshot_sha256`（`src/bridges/profiles/*.py` +
  `app_setup_models.py` + `app_support.py` + `chat/turn.py`）；
- 合同版本：`automatic_extractor=profile-auto-v2`、`signal_classifier=profile-signal-v2`、
  `prompt=profile-extraction-prompt-v4`、recipe/context/quality_policy、
  `atomic_migration=profile-atomic-v1`；
- 预算常量：单轮超时 600s、抽取最大重试 3、最大邻居数 2
  （`src/bridges/profiles/automatic.py`）；
- 凭据存在标记：`qwen_configured/qwen_source`、`tavily_*`（只记来源，不记正文）；
- pipeline/evaluation 摘要为评测代码身份锚点：证据文件与最终提交之间的
  对应关系以摘要为准，不以提交号单独断言。

## 4. 检查项与判定（固定，不因实测调整）

运行器在 `profile_journey_evidence.py:evaluate_journey` 中按旅程逐项断言；
判定规则：全部通过 `pass`；全部失败 `known_failing`；部分通过 `partial`。
基线期望 `known_failing` 接受 `known_failing` 或 `partial`（记录细化），
`baseline_match` 记录一致性。

| 旅程 | 检查项 | 基线期望 |
| --- | --- | --- |
| t01-name / t01-pet / t01-habit | fact_captured（画像记录/条目/观察含目标事实） | known_failing |
| t02-psychology | interest_captured_not_blocked（成功抽取且含「心理学」） | known_failing |
| t03-time-limit | content_captured + time_constraint_captured | known_failing（实测 partial） |
| t04-cross-turn | neighbor_context_present + real_conversation_context_passed + preference_captured（替身模式用录制载荷；真实模式用应用级探测） | known_failing |
| positive-academic-control | extraction_succeeded + four_dimension_written + atomic_item_written + fact_active_in_snapshot | pass |
| recall-academic-control | extraction_succeeded + adopted_in_next_turn（只认最后一轮 `adopted_profile_slice.adopted_items`） | known_failing（实测 partial） |

## 5. 开发语料与留出语料

- 开发语料：T01–T04 场景与八条可运行旅程（`profile_journey_scenarios_baseline.py`、
  `profile_journeys.py:BASELINE_JOURNEYS`），用于基线与日常回归。
- 留出语料：每个场景内 `kind="holdout"` 的独立留出措辞标记（「独立留出措辞」），
  本轮不参与通过/失败判定，留给 18 的真实效果复核；混合主体、否定、同义
  变体用于后续票的针对性检查（结构由 `tests/evaluation/test_profile_journeys.py` 校验）。
- 未支持的新功能一律标 `planned`，不冒称现有缺陷（`不记为现有缺陷` 字样为结构校验锚点）。

## 6. 指标覆盖（验收标准第 5 条）

| 指标 | 来源 |
| --- | --- |
| 准确 | positive-academic-control（抽取与成对写入正确） |
| 遗漏 | t01-name/pet/habit、t03 时间约束、t02 误拦截 |
| 误归属 | t04 会话标识与邻近消息；问句被误判为自述产生的杂项条目（正/召回对照中的实测观察） |
| 修订 | 正/召回对照里的 supersede 链实测；正确修订路径留给 T07/T08（planned） |
| 召回 | recall-academic-control 的 `adopted_in_next_turn`（当前失败，排除原因「与当前问题无关」） |
| 回答收益 | 真实模式回答样本与采用切片对照；替身模式不评价回答质量，因果收益由 18 复核 |
| 错误复发 | 同一语料两次运行（全量 + 抽跑）结论一致；每次运行锁固定版本 |
| 成本 | 运行锁 + `totals`（真实模式 token 实测；替身模式为 0 并明确标注不代表成本） |
| 延迟 | `generation_duration_ms_samples`、`extraction_duration_ms_samples`（实测样本，不编造） |

## 7. 复现与重跑

```powershell
# 全量双模式
python scripts/run_issue01_profile_baseline.py --mode both --proxy http://127.0.0.1:7890 `
    --output .scratch/profile-memory-redesign/validation/01-baseline
# 验收要求：一个已通过 + 一个已知失败的抽跑
python scripts/run_issue01_profile_baseline.py --mode deterministic `
    --journeys positive-academic-control,t01-name `
    --output .scratch/profile-memory-redesign/validation/01-baseline
```

结果必须可追溯到输入（固定语料 + 运行锁）与版本（摘要）；已知失败单独记录，
不用无条件跳过、改预期或长期标红处理。
