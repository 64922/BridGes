# 04 — 统一自动画像写入与原子镜像

**What to build:** 自动提取及其重试通过同一画像提交 module 写入四维内部记录和用户可见原子条目；提交失败不会留下被当作成功的部分画像，下一轮读取保持去重、来源和用户权威。

**Blocked by:** 无，可立即开始。

**Status:** ready-for-human

## 背景

自动提取直接安排 `upsert_automatic_record` 与 `mirror_record`，并在 `_commit_transaction` 中判断具体 SQLite 仓库类型。调用者需要知道存储连接与批次回滚行为，削弱了 seam 的价值。此票只迁移普通自动提取及重试，用户更正和删除留给 05。

## 实施范围

1. 先界定一次提交包含哪些记录：四维来源、原子条目、提取结果/审计及相关观察记录；区分必须同成同败的写入与可保留的尝试记录。
2. 确定最小画像提交 module，将跨记录写入、镜像、事务加入和失败清理移入 implementation；自动提取 caller 保留提取、证据校验与有界重试决策。
3. 接入首次自动写入及后台重试，保留现有空结果、受保护条目、重复来源的返回含义，不把“不写入”误当系统失败。
4. 复用现有 SQLite 和内存 adapter；从这两条调用路径中移除对具体仓库类型、连接和事务开关的知识。
5. 使用真实一轮消息后的提取和下一轮记忆切片读取验证闭环，不只直接调用镜像函数。

## 验收标准

- [x] 合法自动提取产生可追溯四维来源及无类别原子条目；下一轮只按现有最小切片规则使用相关信息。
- [x] 同一事实重复出现只合并必要来源，不增加重复活动条目；不同账户相同正文仍完全隔离。
- [x] 用户编辑过的正文不被自动提取覆盖，已删除事实不因自动提取或重试复活。
- [x] 批次中第一条成功、后续镜像失败的故障测试证明：业务成功结果不会部分提交；尝试/失败审计保留规则明确。
- [x] 故障后按既有重试策略执行，不生成重复条目、虚假成功或无法清理的提取状态。
- [x] SQLite 共用连接时不出现嵌套 BEGIN 错误；内存 adapter 回滚覆盖同一组业务对象，而非只回滚其中一个仓库。
- [x] 首次提取与重试不再直接判断 SQLite 实现类型或拼接两种记录的提交顺序。
- [x] 既有原子画像、自动提取和重试相关回归通过，未要求 05 或 06 一并完成才能保持可用。

## 验证建议

复用原子画像及其请求入口的现有测试基线，增加通过提取入口执行的镜像故障和重试测试。用同一组核心场景覆盖内存和 SQLite；SQLite 使用独立临时目录。无需真实模型，使用已有确定性提取/模型 adapter。

## 范围限制与交付

不修改提取范围、阈值、提示词、自动提取时机或隐私说明；四维记录继续作为内部提取与迁移来源。不得为减少分支创建全项目通用工作单元框架。交付写明提交集合、失败可见性、已迁移入口与 05 尚待迁移的更正/用户操作。

## Comments

两个真实 adapter 已存在，本任务加深现有 seam，不需要臆造第三种存储实现。

## 执行记录（2026-09-28，分支 `codex/04-profile-extraction-commit`）

### 1. 提交集合与不变量

新增 `src/bridges/profiles/commit.py`：

- `ProfileRecordSubmission`：一条待提交的四维记录（维度、正文、动作、把握度、证据引用、迁移版本），字段与 `upsert_automatic_record` 一一对应，不含任何策略默认值。
- `ProfileCommit.transaction()`：一次提交的边界。参与方是抽取记账仓库（`AutomaticProfileRepository`）、四维记录（`FourDimensionProfileService`）与原子条目（`AtomicProfileService`；未挂载时缺席）。
- `ProfileCommit.write_records()`：按顺序「写四维记录 → 立刻镜像原子条目」，返回去重后的记录标识。

**同成同败的写入**：四维来源记录、原子镜像，以及调用方在同一 `transaction()` 内写入的提取记账（运行/任务状态、观察记录、模型运行锁）。任一步失败就整批回滚，不留下会被后续读取当成成功的部分画像。

**可保留的尝试记录**：失败之后由调用方另开事务写入的记账——重试任务、运行的 `pending`/`exhausted` 状态、失败审计与运行锁（`_schedule_retry` 与重试耗尽路径）——它们是重试依据而不是成功结果。

**事务形态由 adapter 声明**：新增 `src/bridges/profiles/transactions.py::joined_transaction()`，与既有 `TaskQueue.enqueue` 是同一规则；三个 SQLite 仓库（记账、四维、原子）共用同一个 `BridgesDatabase` 时并入外层事务，内存仓库各自建立可回滚快照。`AutomaticProfileService` 不再判断仓库类型、连接或事务开关。

### 2. 已迁移入口

1. **首次自动写入**（`_commit_output`）：不再自行拼「`upsert_automatic_record` → `mirror_record`」，改为回到提交 module。
2. **后台重试**（`_run_retry_task`）：同一提交边界、同一成对写入。
3. **纠正路径**（`_preprocess_correction`）：事务边界同一化；写入顺序与 `mirror_record` 调用保持不变。
4. **顺带收口**：`AutomaticProfileRepository.durable_queue()` 让「有没有跨重启重试队列」由 adapter 声明，`AutomaticProfileService.__init__` 里最后一处 `isinstance(..., SqliteAutomaticProfileRepository)` 随之删除；`AtomicProfileService.transaction()` 为批量提交暴露原子仓库边界（供 05/06 复用）。

结构性证据：`AutomaticProfileService` 内已无仓库实现类型判断（仅剩对 error/extractor/output 的 isinstance）；`automatic.py` 内不再出现 `upsert_automatic_record`，仅存的 `mirror_record` 调用在 `_preprocess_correction`（05 范围）。

### 3. 失败可见性

| 情况 | 业务结果 | 记账与审计 |
| --- | --- | --- |
| 批次任一步失败（含镜像失败） | 四维记录、原子条目、观察记录全部回滚 | run/task = `pending` + 错误码；审计 `RETRYABLE_FAIL` |
| 重试再次失败并耗尽 | 同上，且不产生任何成功结果 | run/task = `exhausted`，run.outcome = `permanent_failure`；审计 `BLOCKED` |
| 镜像不写入（正文为空、用户已改成别的正文） | 四维记录照常写入并计入 `committed_record_ids` | 审计 `SUCCESS`（不算提交失败） |
| 用户已删除的事实再次出现 | 四维仓库拒绝复活并抛出 | 走有界重试 → 耗尽，条目保持删除状态 |

### 4. 05 尚待迁移的更正/用户操作

- 聊天更正的跨记录编排：`_preprocess_correction` 仍是显式「`correct_record` → `mirror_record`」序列（现已落在同一提交边界内，但未走 `write_records`）。
- 显式记住/忘掉（`AtomicProfileService.remember/forget`）、行内编辑 `modify_item`、删除 `delete_item` 与来源撤回 `_withdraw_source_record` 的跨记录顺序未动。
- 已为 05 备好的接缝：`ProfileCommit.write_records()`（普通写入 + 镜像）与 `AtomicProfileService.transaction()`（批量提交边界）。

### 5. 验证（命令与结果）

```bash
# 本票测试：内存与 SQLite 各跑一遍（含故障窗口与重试闭环）
python -m pytest tests/profiles/test_issue04_profile_commit.py -q          # 12 passed
# 既有原子画像与自动抽取回归
python -m pytest tests/profiles tests/closeout -q --tb=no -rf              # 7 failed / 430 passed
python -m pytest tests/profiles/test_v2_08_atomic_profile.py     tests/profiles/test_v2_08_atomic_profile_api.py -q                     # 54 passed（含本票 12 条）
# 类型与风格
python -m mypy src/bridges/profiles/{commit,transactions,atomic,automatic,four_dimensions}.py     tests/profiles/test_issue04_profile_commit.py                          # Success: no issues found in 6 source files
python -m ruff check src/bridges/profiles/                                 # 与基线逐条相同（29 条既有 E501/I001，无新增）
```

`tests/profiles tests/closeout` 的 7 条失败在基线工作树（f04c79c）逐名相同（`closeout` 的邮件/能力清单/三旅程与 `profiles` 的更正是既有失败）。

**新增用例确实约束本次修复**：把本票测试文件放进 f04c79c 的 detached 工作树先跑一遍 → `3 failed / 9 passed`（内存故障窗口 2 条 + 混合 adapter 1 条失败），另外 9 条是同口径回归守卫（SQLite 侧「只开一次事务」、去重/账户隔离、用户编辑、删除不复活）。

### 6. 全量回归（两侧同跑法）

命令（两侧相同：不 deselect、不 ignore、仓外 basetemp）：

```bash
python -m pytest -q --basetemp=<仓外临时目录>
```

| | 失败 | 通过 | 跳过 | 收集 | 耗时 |
| --- | --- | --- | --- | --- | --- |
| 分支 `codex/04-profile-extraction-commit` | 252 | 3824 | 39 | 4115 | 14:35 |
| 基线 `main` f04c79c（detached 工作树） | 252 | 3812 | 39 | 4103 | 19:52 |

- Δ收集 +12 = 本票新增用例文件（12 条全绿）；Δ通过 +12；Δ跳过 0。
- 失败名称双向 diff 各剩 1 条互不相同的名字，且两条都与本票无关，隔离复跑两侧各 `2 passed`：
  - 基线侧独有 `tests/closeout/test_arxiv_worker_reliability.py::test_handshake_timeout_maps_to_arxiv_handshake`；
  - 分支侧独有 `tests/image/test_media_edge_model_run_locks.py::test_alt_text_edit_links_task_and_existing_asset`。
  两条都是负载型抖动（本轮机器上同时跑着其他会话的应用与测试），不接触画像路径。
- 日志留档：`.tmp/issue04/raw/{baseline,branch}.log`、逐名清单 `{baseline,branch}.names`、提取脚本 `.tmp/issue04/extract_failures.py`。第一次分支全量因 **C 盘写满**（`OSError: Errno 28`）无效，已清理历史 basetemp 后重跑并以上表为准。

### 7. 两轴评审（Standards / Spec）与处理

**已修**：

1. Spec 轴：核心场景（下一轮切片、去重与账户隔离、用户编辑、删除不复活）原来只在内存 adapter 上断言，与「用同一组核心场景覆盖内存和 SQLite」不符 → 改为 `harness` 参数化 fixture，同一组场景内存与 SQLite 各跑一遍；SQLite 故障用例同时补上失败审计断言。
2. Standards 轴：`SqliteAutomaticProfileRepository.transaction()` 仍是裸 `database.transaction()`，与另外两个仓库的并入规则不对称（提交边界若落在外层事务内会嵌套 `BEGIN` 报错）→ 三个 SQLite 仓库统一走 `joined_transaction`。
3. Spec 轴：`commit.py` 的 docstring 把「用户已删除的事实」也写成「镜像不写入、不算失败」，与实际的「四维仓库拒绝复活并抛出」不符 → 明确区分「镜像跳过（正常结果）」与「撤回拒绝（可重试失败）」。
4. Standards 轴：docstring 自称「提取记账的唯一提交归属」，而记账行仍由服务写在自己的端口上 → 改述为「提交边界归属地」，并写明调用方在同一边界内写记账。
5. 顺带：撤销 `ruff --fix` 带来的无关 import 重排，只保留本票真正需要的两处 import 变化（外科式改动）。

**决定不改**：

- `ProfileRecordSubmission` 与 `upsert_automatic_record` 的字段一一对应（评审提示「加字段要改三处」）：保留显式映射，换来 mypy 对下游签名的逐项校验；换成 `**kwargs` 透传会丢掉这层检查。
- 纠正路径仍在服务里显式拼接写入：本票范围明确只迁移普通提取与重试，序列收口留给 05；共同规则（事务边界）已经只有一份。
- `durable_queue()`/`transaction()` 这两个新的小接缝：服务于验收项「不再直接判断 SQLite 实现类型」，不是通用工作单元框架（`transactions.py` 仅 29 行，只被画像仓库使用）。

### 8. 未完成与残余风险

- 第一次分支全量因 **C 盘写满**失败（清理了本机历史 basetemp 后重跑通过）。为避免误删其他会话的在跑临时目录，只清理了本票与历史轮次自己的 basetemp。
- 混合 adapter 组合（状态仓库 SQLite、画像仓库内存）在本票里由测试证明整批回滚一致；生产接线仍让三个仓库共用同一个库，未引入新的组合方式。
- `AtomicProfileService.transaction()` 目前只有提交 module 使用；如果 06 的 adapter 一致性工作需要不同的事务形状，应在那张票里重新评估而不是改这里。
