# 06 — 闭合画像失败恢复与存储一致性

**What to build:** 原子画像提交中断后，重新启动、再次处理或重放旧消息都遵循一致规则；SQLite 与内存实现给出相同业务结果，用户删除/编辑与历史迁移仍受保护。

**Blocked by:** 05 — 统一画像更正、编辑与撤回。

**Status:** ready-for-human

## 背景

04、05 将入口迁入共同 module 后，还需证明其承诺跨越故障和存储实现。尤其是墓碑已落地但来源撤回未完成时，仅靠进程内异常处理不构成跨重启恢复保证。本票交付恢复行为，同时收口迁移造成的内部兼容入口。

## 实施范围

1. 从 04、05 明确的提交集合和保护点生成故障矩阵，检查自动写入、纠正、编辑、删除与多条忘掉的失败窗口。
2. 在持久记录可识别的残留状态上接续恢复；若现有操作重试/恢复路径已足够则直接复用，否则增加最小本地恢复能力，不引入通用任务框架。
3. 使来源撤回失败在重新建立 module 后仍可修复，修复不依赖已丢失的内存对象；不存在来源时保持受控幂等结果。
4. 将共同合同场景应用于现有内存与 SQLite adapter。允许事务机制不同，但最终可见业务结果和失败后保护规则一致。
5. 验证旧四维数据迁移、迁移对账/回滚、历史导出和旧消息安全重放，不把这些历史能力强行改造成新提取路径。
6. 清点当前生产写入口，移除本轮产生的重复提交编排和无用内部转发；保留历史数据及受迁移门保护的兼容入口。

## 验收标准

- [x] 同一批次自动镜像中断后，重试不产生重复活动条目或“已成功但只写一半”的结果；两个 adapter 遵守同一合同。
- [x] SQLite 中提交墓碑后模拟来源撤回失败，关闭并重建连接/module 后，恢复能补齐来源处理，条目全程不回到活动列表或切片。
- [x] 多条忘掉的部分失败可重复恢复，已完成项不重复变更，未完成项可识别，不误处理其他账户条目。
- [x] 故障期间或恢复前发生的新用户编辑不会被较旧提取结果覆盖；删除后重放旧消息不会复活事实。
- [x] 迁移重复运行、来源已撤回、用户已编辑、迁移回滚和历史导出继续符合既有合同；不删除或改写旧四维审计来源。
- [x] 自动提取、重试、更正、编辑、记住/忘掉和删除的当前生产入口均不再决定跨记录事务顺序或判断具体仓库类型。
- [x] 04、05 的验证场景及原子画像、自动提取、更正、安全重放、迁移恢复相关回归通过。
- [x] module 的 interface 文档与实现一致，明确失败是否已越过墓碑保护点、能否重试以及恢复后的可见结果。

## 验证建议

以真实临时 SQLite 的关闭/重开验证持久恢复，以内存 adapter 验证同一业务合同。保留对去重、来源、版本、墓碑、提取结果的联合断言；不要求两种 adapter 的内部调用序列相同。只补尚缺的失败和恢复测试，避免另建覆盖相同实现细节的测试体系。

## 范围限制与交付

不修改迁移版本或历史数据格式，除非恢复的最小实现确实需要，并附迁移与回滚证据；不得借机清理历史画像体系。交付包含故障矩阵、恢复入口、adapter 一致性结果、遗留入口核查和已运行测试说明。

## Comments

报告中的 42 项通过是正常路径与既有保护的基线，不替代本票的跨重启故障恢复证据。

## 执行记录（2026-09-28，分支 `codex/06-profile-recovery-and-adapter-parity`）

### 1. 故障矩阵（实施范围 1）

故障注入落在真实提交点（四维来源撤回、原子镜像、条目保存、重放任务），不 mock 调用顺序；「重启」对 SQLite 是关库重开（连接与三个仓库全新，只剩文件里的记录），对内存是复用同一组仓库、丢掉整个服务层。

| # | 故障点 | 落在保护点 | 故障时的可见结果 | 重试／恢复 | 覆盖用例（双 adapter） |
| --- | --- | --- | --- | --- | --- |
| 1 | 批内四维记录写入失败 | 之内 | 整批回滚：记录、镜像与提取记账同成同败 | 整轮重试（04 已覆盖，本票回归） | 04 用例 + 本票 #2 同族 |
| 2 | 批内第二条事实镜像写入失败 | 之内 | 同上；运行停在 `PENDING`/`PENDING_RETRY`，`committed_record_ids` 为空——没有「已成功但只写一半」 | 生产重试路径（`run_retry_tick`）在同一运行边界内一次补齐 | `test_interrupted_mirror_retry_does_not_duplicate_or_half_write` |
| 3 | 行内编辑保存失败（换键 + 抑制键同事务） | 之内 | 正文、身份键、乐观锁版本全回滚；旧正文没被写成抑制键 | 用户重新编辑；后续自动写入仍合并到同一事实 | `test_inline_edit_fault_window_keeps_text_key_and_unsuppressed_old_text` |
| 4 | 删除的墓碑已提交、来源撤回失败 | 之后 | 条目保持删除（列表与切片都没有它），底层来源仍活动＝持久残留 | 重启后 `recover_source_withdrawals` 幂等补齐；`delete_item` 原样上抛、墓碑有效 | `test_restart_recovery_completes_source_withdrawal_after_delete` |
| 5 | 忘掉命中两条、其中一条撤回失败 | 之后 | 两条条目都删除成立；未完成项可识别（来源仍活动 + `FAILED` 结论） | 重复恢复只补未完成项，已完成项版本不动；账户作用域不越界 | `test_multi_forget_partial_failure_is_recoverable_and_account_scoped` |
| 6 | 故障期间用户编辑 + 旧消息重放（含恢复后按真实重放形状再放一次） | 之后 | 用户正文保持当前版本，被删事实不复活（列表、切片、来源状态三处） | 恢复只补来源，不改正文；重放走 `from_replay` 保护点 | `test_recovery_keeps_user_edits_and_never_revives_deleted_facts` |
| 7 | 恢复之后重跑迁移／回滚 | 历史能力 | 迁移幂等、对账摘要一致、旧四维审计来源逐字段不变 | `migrate_account` / `rollback_migration` 原文不动 | `test_history_migration_contracts_hold_after_recovery` |

### 2. 恢复入口与持久残留（实施范围 2、3）

- **残留状态只从持久记录推导**：`AtomicProfileService.tombstoned_items_with_sources(account_id)` 扫描「`status=withdrawn` 且 `source_record_id` 非空」的条目；墓碑保留来源标识，所以待补工作不依赖失败时的进程内对象。
- **恢复入口**：`ProfileCommit.recover_source_withdrawals(account_id)`（`AtomicProfileService.recover_source_withdrawals` 转发）＝「扫描墓碑 → 幂等重跑 `withdraw_item_sources`」，结论逐条返回 `SourceWithdrawal`（`WITHDRAWN` / `ALREADY_WITHDRAWN` / `NO_SOURCE` / `FAILED`）。没有新增存储、迁移号未动（`SCHEMA_VERSION` 仍 59），没有通用任务框架，也不需要台账。
- **无来源时的受控结果**：未匹配到四维记录的「记住」条目没有可撤回来源，扫描直接跳过；已收敛项重复调用返回 `ALREADY_WITHDRAWN`，不重写业务结果。
- **失败明细的位置**：逐条结论在返回值与服务端日志（`WARNING 画像来源撤回失败（可安全重试）`）；`forget` 的契约结果保持冻结不改，明细不并入用户可见结果。**本票不新增生产调度调用点**（见 §7 决定 1）。

### 3. adapter 一致性结果（实施范围 4）

- 同一组合同场景在内存与 SQLite 双栈同结论：本票 6 个用例 × 2 adapter 全通过，断言只看业务结果（条目去重、来源状态与版本、墓碑、切片、迁移对账），不看内部调用序列。
- 观察到的内部差异（不违反合同，写进测试注释）：内存仓库返回活对象，失败注入下同一对象会被就地改写，测试需先快照值再断言；SQLite 每次读取都是新的行记录。恢复语义两者相同——`ALREADY_WITHDRAWN` 与版本不变都成立。
- 事务机制差异：SQLite 三仓库并入同一外层事务（`joined_transaction`），内存仓库各自快照回滚；保护点之后的撤回在两种 adapter 上都是「墓碑已提交、撤回逐条隔离」。

### 4. 历史能力核查：迁移、回滚、导出、重放（实施范围 5）

- **迁移与回滚**：恢复之后 `migrate_account` 首次 `(migrated, duplicated, tombstoned) = (1, 1, 1)`、第二次 `migrated=0` 且对账摘要一致；`rollback_migration` 只删除本批新建的条目，用户删除与镜像产物保持；旧四维来源逐字段未被删除或改写。
- **历史导出**：`ProfileService.export_profile_data`（legacy assertions + 版本历史 + 治理审计）不在本票改动面内——改动文件只有 `profiles/{commit,atomic,automatic}.py` 与两份测试，未触碰 assertions/audit 表、导出实现与 410 路由；导出合同由既有 `tests/profiles/test_profile_governance.py::TestProfileExport`、`tests/profiles/test_profile_center.py` 守护，本次改动后 `tests/profiles` 全量定点运行通过（唯一失败为既有失败，见 §8）。
- **旧消息安全重放**：本票的重放断言走真实形状（来源哈希内嵌 `:replay-v1:` + 重放任务 + `run_retry_tick`），不再是「新消息重抽」的同义替代；05 的重放保护用例一并回归通过。

### 5. 生产写入口清点（实施范围 6、交付「遗留入口核查」）

| 入口 | 位置 | 跨记录事务顺序 | 仓库类型判断 |
| --- | --- | --- | --- |
| 自动提取 | `AutomaticProfileService.preprocess_message` | 交提交 module（`_commit.transaction()` + `write_records`，`automatic.py:1632/2520`） | 无 |
| 后台重试 | `run_retry_tick` → `_run_retry_task` | 同上（`automatic.py:2195`） | 无 |
| 聊天更正（首次与重试共用） | `_preprocess_correction` → `_apply_correction` → `write_correction`（`automatic.py:1704`） | 交提交 module | 无 |
| 行内编辑 | `AtomicProfileService.modify_item` | 单记录写入（条目换键 + 抑制键同事务），无跨记录顺序 | 无 |
| 记住 | `AtomicProfileService.remember` | 单记录写入（解除同键墓碑） | 无 |
| 忘掉 | `AtomicProfileService.forget` | 墓碑在 `_profile_commit.transaction()` 内，撤回在边界外逐条（`atomic.py:1015/1021`） | 无 |
| 删除 | `AtomicProfileService.delete_item` | 同上（`atomic.py:917/926`） | 无 |
| 来源撤回／恢复 | `withdraw_item_sources` / `recover_source_withdrawals` | 交提交 module 归属 | 无 |

- **本轮产生的重复编排已收口**：更正「写入 → 结果与结论」的映射收成 `_apply_correction` 一份（首次处理与后台重试共用），删除 `_preprocess_correction` 中被覆盖后再没被读到的 `record_ids` 初值；`withdraw_item_sources` 不再是「只被测试使用的包装」——`delete_item`、`forget` 与恢复流程都走它。
- **遗留与受保护入口（保留原样）**：四维页面路由（`profiles/api.py` 的 PATCH/DELETE 四维记录）、聊天降级链路（未挂四维服务时的 `ProfileService.process_conversation_message`）、410 legacy 路由与迁移／回滚端点全部未改；`correct_record` 在生产只由 `commit.write_correction` 调用。
- **仓库类型判断**：入口层 grep 无 `isinstance(...Repository)` / 连接 / 事务开关判断；唯二的类型决策在接线处（`api/main.py:1117-1140`）与既有 `replay_repository.py:94`（重放候选仓库，非本票入口）。

### 6. 测试与验证

```bash
# 本票用例（内存与 SQLite 双栈）
python -m pytest tests/profiles/test_issue06_profile_recovery.py -q     # 12 passed
# 画像目录定点回归（含 04/05/迁移/导出/治理）
python -m pytest tests/profiles -q                                      # 1 failed / 352 passed（失败与基线同名单）
# 类型与风格
python -m mypy src/bridges/profiles/{commit,atomic,automatic}.py tests/profiles/test_issue06_profile_recovery.py tests/profiles/test_issue05_profile_user_authority.py  # Success
python -m ruff check <5 个改动文件>                                      # 14 条（全部为 automatic.py 既有 E501/I001，与基线逐条同签名）
```

- **区分力**：同一测试文件放到基线树（`cb10ae8`）运行 → 8 failed / 4 passed；失败的都是需要恢复入口的用例（`AttributeError: recover_source_withdrawals`），另 2 个（镜像中断重试、编辑故障窗口）在基线也通过——它们是既有合同回归，不是新能力证据。基线树另有探针脚本实证「只能靠私有内部访问勉强模拟恢复」，留档主仓 `.tmp/issue06/raw/`。

### 7. 两轴评审（Standards / Spec）与处理

评审针对 `cb10ae8...HEAD` 的改动，两个轴（Standards／Spec）并行出具，逐条处理如下。

**已修**：

1. Spec：AC1 的镜像中断场景没有用例——`_MirrorFailsOnce` 建了却没注入。补 `test_interrupted_mirror_retry_does_not_duplicate_or_half_write`（整批回滚、`PENDING` 而非「已成功但只写一半」、生产重试路径补齐、再次重试无待处理任务且条目标识与版本不变）。
2. Spec：AC4 的「重放」原本是新消息重抽的同义替代，没走真实重放路径。改为按真实形状（来源哈希内嵌 `:replay-v1:` + 重放任务 + `run_retry_tick`）在恢复之后再放一次。
3. Standards：`withdraw_item_sources` 只被测试使用，属实施范围 6 要移除的「无用内部转发」。改为 `delete_item`、`forget` 与恢复流程都走它，包装方法成为服务层的单一归属地。
4. Standards：`_preprocess_correction` 里 `record_ids: list[str] = []` 初值被 `_apply_correction` 覆盖后再没被读到——删除。
5. Standards／Spec：`tombstoned_items_with_sources` 的 docstring 说「用户单独记住的条目没有来源」，但 `remember` 命中既有四维记录时会带 `source_record_id`（删掉后同样应撤回来源）——改成按「有没有关联来源」表述，行为不变、文档与实现一致。
6. Spec：AC8 还要求入口侧也说明失败语义。`AtomicProfileService` 类文档补上「失败是否越过墓碑保护点、能否重试、恢复后可见结果」的指向（细节仍在提交 module 的 module docstring）。

**决定不改（附理由）**：

1. **恢复入口暂无生产调用方**：本票交付的是恢复能力与合同（AC2 由测试在重建 module 后驱动入口证明），工单明确「不引入通用任务框架」，且用户可见结果（原子条目、记忆切片）在墓碑提交时已经正确——残留只体现在四维记录状态。入口按账户、幂等，任何后续维护动作（应用启动时的账户级维护、抽取重试 tick）都可直接调用，接线留给下一张票决定。
2. **恢复明细不落台账**：逐条结论随返回值与日志给出；残留状态本身可重复识别，不需要新表。
3. **`ProfileCommit.recover_source_withdrawals` 只做一行扫描转手**：评审提出「Middle Man」。保留的理由是恢复与撤回必须同属保护点之后的语义归属地（同一 module 回答「失败落在保护点哪一侧」），服务层只是转发公开入口。
4. **测试 harness 与 05 有重复（`_MirrorFailsOnce`、`_TwoFactExtractor`、常量）**：与 04／05 各自复制 harness 的既有约定一致，抽 conftest 会顺手改动 04／05 的测试文件，超出本票范围。
5. **提交 module 的 module docstring 讲了三遍同类语义**：评审建议删一份。这是 AC8 的交付物本身（失败侧、重试可能、恢复可见结果各说一次），保留。

### 8. 全量回归（同跑法：不 deselect、不 ignore、仓外 basetemp、`PYTHONPATH=src` 绝对路径）

| 树 | 失败 | 通过 | 跳过 | 耗时 | 进度字符单写者校验 |
| --- | --- | --- | --- | --- | --- |
| 分支 `f020312`（基线点 `cb10ae8` + 本票） | 251 | 3883 | 37 | 20:40 | `.`3883+`F`251+`s`37=4171 ✓ |
| 基线 main `cb10ae8`（detached 工作树） | 251 | 3871 | 37 | 18:06 | `.`3871+`F`251+`s`37=4159 ✓ |

- **失败名单双向 diff 为空**（两侧各 251 条，`comm` 两个方向都没有输出）；Δ通过 +12 = 本票新增用例 6 个 × 2 adapter，Δ跳过 0（两棵树都在工作树里有生产构建，`NEEDS_WEB_BUILD` 一视同仁）。
- 旁证：分支失败名单与并行票（issue-03）留档的 main 参照名单（`.tmp/issue06/raw/ref-main.names`，251 条）逐名相同；`tests/profiles` 唯一失败 `test_issue01_chat_profile_correction.py::test_chat_correction_uses_latest_record_and_is_idempotent` 两侧同名单。
- **环境因素（与本票无关，但影响这一类测试能否跑完）**：`tests/integration/test_runtime_smoke.py::test_start_fails_*` 会清空全部 `BRIDGES_*` 环境变量，于是 `cli.main start` 落回真实数据目录 `%LOCALAPPDATA%\BridGes\data`。真库锁被别的实例持有时它按预期失败（分支运行时正是这个条件：失败名单里的既有失败）；锁空闲时它会**真的启动一个桌面实例**并写真实数据目录，而测试用 `subprocess.run` 等它退出——永不返回。基线首次复跑就卡在这里（已终止进程树并清理孤儿 api/worker/scheduler），补跑时由我持真库锁复现分支运行时的条件，运行正常完成。运行日志与名单留档主仓 `.tmp/issue06-verify/`。



