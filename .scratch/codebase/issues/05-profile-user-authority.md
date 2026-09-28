# 05 — 统一画像更正、编辑与撤回

**What to build:** 用户通过聊天更正、记住/忘掉或设置页编辑/删除画像后，原子条目及其来源遵守同一用户权威规则；旧消息重放不能恢复已撤回或已改掉的内容。

**Blocked by:** 04 — 统一自动画像写入与原子镜像。

**Status:** ready-for-human

## 背景

用户更正当前显式安排四维纠正与原子镜像；编辑另行管理旧正文抑制键；删除和忘掉先写墓碑，再事务外撤回来源。顺序是有意的防复活保护，应集中归属并验证，不应简单改成“所有写入一个事务”后丢失保护。

## 实施范围

1. 核查聊天更正首次处理与重试、显式记住/忘掉、行内编辑与删除的现行返回语义，列出真实写入口。
2. 让上述入口通过画像提交 module 执行共同写入规则，保留指令识别、目标匹配与用户请求校验的原职责。
3. 集中版本冲突、去重、旧正文抑制、墓碑及来源撤回规则。同步“记住/忘掉”仍在本轮提交前生效，不延后到下一轮异步处理。
4. 明确删除的保护点与恢复责任：墓碑已提交后来源撤回失败，用户可见条目仍不可召回；后续补偿不得重新激活旧事实。
5. 为重试同一撤回操作提供可验证的幂等语义。若现行调用已能可靠恢复，复用其机制；跨重启的剩余恢复闭环由 06 完成。

## 验收标准

- [ ] 聊天明确更正首次处理及重试得到一致的原子条目与来源；无法定位或受用户编辑保护时保留现行未解决/受保护结果。
- [ ] 行内编辑保留版本检查、重复正文冲突和账户隔离；旧正文通过抑制规则不被旧消息重新生成。
- [ ] 用户记住的信息立即按现行本轮合同生效；重复指令不会创建重复活动条目。
- [ ] 删除和忘掉后，列表与后续记忆切片均不再返回目标事实；未命中或目标不明确时不误删。
- [ ] 来源撤回失败的故障场景中，已提交墓碑保持有效；不能为追求全量回滚而使已删除条目重新可见。
- [ ] 同一撤回操作重复执行不会重复写业务结果或覆盖新用户编辑；多条忘掉场景中的部分来源失败有明确结果和可恢复依据。
- [ ] 编辑、删除与自动镜像竞争时，用户权威和版本合同仍然成立。
- [ ] 用户入口无需自行知道“先写哪张记录、再撤回哪个来源”；04 以及现有用户操作回归通过。

## 验证建议

分别从聊天指令和设置请求入口验证，再以列表、记忆切片、内部来源与墓碑观察结果。故障注入选择真实提交点，内存/SQLite 都覆盖墓碑后的来源失败。避免仅断言 mock 调用顺序。

## 范围限制与交付

不改变指令语法、目标匹配阈值、用户操作文案或前端布局；不恢复固定类别界面。不要求删除现有四维数据。交付列出用户权威规则、保护点、操作重试语义，以及 06 需验证的重启恢复形态。

## Comments

墓碑先写属于必须保留的安全意图；具体事务形状可变，防复活保证不能削弱。

## 执行记录（2026-09-28，分支 `codex/05-profile-user-authority`）

### 1. 现行写入口核查（实施范围 1）

| 入口 | 位置 | 现行返回语义 |
| --- | --- | --- |
| 聊天更正首次处理 | `AutomaticProfileService._preprocess_correction` | `UNRESOLVED` / `NO_ACTIVE_RECORD` / `PROTECTED`（纠正次数≥3）/ `WRITTEN` / `FAILED`；成功时「纠正四维记录 + 镜像原子条目」 |
| 聊天更正后台重试 | `AutomaticProfileService._run_retry_task` | 状态机同上，但**只写四维记录、不镜像**（与首次不一致的真实缺陷，本票修复） |
| 更正结果重放 | `_replayed_correction_result` | 返回记账结果，不重新写入 |
| 显式记住/忘掉 | `parse_memory_directive` + `AtomicProfileService.remember/forget`（`_memory_directive_result` 同步处理） | `REMEMBERED` / `FORGOTTEN` / `UNRESOLVED`；本轮生成前生效 |
| 行内编辑 | API `PATCH /profile/items/{id}` → `modify_item` | 版本冲突、重复正文冲突、换键后写抑制键 |
| 删除 | API `DELETE /profile/items/{id}` → `delete_item` | 墓碑先提交，再撤回来源；重复删除报「已删除」 |
| 来源撤回 | 原 `atomic._withdraw_source_record`（本票移入提交 module） | 已撤回/无来源静默跳过，存储异常上抛 |
| legacy 降级链路 | `ProfileService.process_conversation_message` | 仅在未挂四维服务时走（`chat/service.py` fail-open）；不在本票范围 |

### 2. 用户权威规则与归属（实施范围 2、3）

- **成对写入（聊天更正）**：新增 `ProfileCommit.write_correction(account_id, *, dimension, content)`——目标定位、保护阈值与版本校验留在四维服务（返回 `(record, changed)` 不变），「记录已改、镜像跟上」由提交 module 保证；首次处理与后台重试共用同一实现，重试不再只写来源。
- **来源撤回**：新增 `ProfileCommit.withdraw_item_sources(account_id, items)` 返回逐条 `SourceWithdrawal`（`WITHDRAWN` / `ALREADY_WITHDRAWN` / `NO_SOURCE` / `FAILED`，`FAILED` 携带原始异常并记 WARNING 日志）——`delete_item` 与 `forget` 共用，原私有实现删除。指令识别（`parse_memory_directive`）、目标匹配（`_matches_target`）、用户请求校验（版本号/正文合法性）保留原职责。
- **单库规则留原位**：版本冲突、条目去重、旧正文抑制键、墓碑写法仍归 `atomic.py`；账户隔离归仓库层。提交 module 只负责「先写哪张记录、再撤回哪个来源」。
- `ProfileCommit` 的参与方改为「已挂载者依次并入」：自动提取的边界含记账仓库（`state`），用户权威操作只挂跨记录两方；`AtomicProfileService` 未注入提交 module 时自建（`records`+`items`），生产接线不变。

### 3. 删除的保护点与重试语义（实施范围 4、5）

- **保护点** = 墓碑提交边界（`commit.transaction()` 内完成校验与墓碑写入）；来源撤回在边界之外逐条执行，撤回失败不回滚墓碑。
- **失败可见性**：`delete_item` 的撤回失败原样上抛（API 500 语义不变，墓碑保持有效）；`forget` 的撤回逐条隔离——部分失败不中断其余条目、不改变本轮 `FORGOTTEN` 结果（用户可见的删除成立，模型上下文据此不再引用已删内容），失败明细随 `withdraw_item_sources` 返回值与日志可查。
- **重试语义**：同一撤回重复执行幂等——已撤回/无来源收敛为非 `FAILED` 结论，不重复写业务结果、不影响用户新建或编辑的条目（测试覆盖：删除后重新「记住」同一事实，再重跑旧撤回不动新条目）。
- **06 的恢复闭环输入**：跨重启后扫描墓碑条目（`status=withdrawn` 且 `source_record_id` 非空）重跑 `withdraw_item_sources` 即可补齐来源撤回；多条忘掉的部分失败明细目前只在返回值与日志，建议 06 把它接入持久化记账。

### 4. 重放保护「旧消息重放不能恢复已改掉的内容」（票头要求）

- **基线缺陷（在 7c5d04b 基线树直接复现）**：重放任务会重新抽取旧正文，`upsert_automatic_record` 按稳定来源键命中既有记录后按证据阶梯覆盖 `correction_count` 1–2 的记录，原子条目跟着回退——聊天纠正被旧消息重放悄悄改回。
- **修复**：重放任务来源哈希内嵌重放标记（`replay._replay_source_hash` 产出 `{原哈希}{:replay-v1:}{原运行标识}`），`_run_retry_task` 判定后经 `write_records(from_replay=…)` 传入 `upsert_automatic_record`：`from_replay=True` 且 `correction_count > 0` 时拒绝改回旧值（返回既有记录）。新消息的自动写入不受影响，既有证据阶梯保持（`≥3` 冻结不变，阈值未动）。
- 行内编辑的既有防护（条目 `user_edited_at` + 旧正文抑制键）已覆盖「设置页改掉」的半边，未改。

### 5. 测试与验证

```bash
# 本票测试（内存与 SQLite 双栈，故障注入在真实提交点）
python -m pytest tests/profiles/test_issue05_profile_user_authority.py -q   # 28 passed
# 既有回归（含 04/更正/安全重放）
python -m pytest tests/profiles tests/closeout -q --tb=no -rf               # 7 failed / 458 passed
python -m pytest tests/chat tests/plugins -q --tb=no -rf                    # 140 failed / 557 passed
# 类型与风格（与 main 的 profiles 目录 ruff 签名逐条一致；新增文件 0 条）
python -m mypy src/bridges/profiles/{commit,atomic,automatic,four_dimensions}.py tests/profiles/test_issue05_profile_user_authority.py   # Success
```

- 7 条失败与基线工作树（7c5d04b）逐名相同（closeout 邮件/能力清单/三旅程/authenticity_gate + profiles 更正既有失败）。
- **区分力**：只含旧 API 的三用例探针在基线树 3 条全失败（更正重试不镜像、忘掉部分失败直接上抛、`from_replay` 参数不存在）；基线树直证「纠正后旧消息重放把 目标B 改回 目标A」。

### 6. 两轴评审（Standards / Spec）与处理

**已修**：

1. Standards：重放判定最初用 `task.source_hash.endswith(标记)`，而真实重放哈希形状是 `{哈希}{标记}{原运行标识}`（`replay._replay_source_hash`），生产路径恒为假——改为内嵌标记 `in` 判定，测试改用真实形状构造（评审抓到的最重问题）。
2. Spec：`withdraw_item_sources` 对「读来源时存储故障」与「对象不存在」的边界无测试守护——补 `get_record` 存储故障注入用例：故障落 `FAILED`（非 `ALREADY_WITHDRAWN`）、墓碑保持、故障解除后幂等补做成功；实现层同步补 `get_record` 非 `ProfileError` 异常的逐条隔离。
3. Standards：`automatic_source_record_id` 仅一处调用且测试未引用——降为模块私有 `_automatic_source_record_id`。

**决定不改**：

- `forget` 不把撤回失败明细并入契约结果（`AtomicProfileMemoryResult` 契约冻结，`extra="forbid"`）：用户可见删除已成立，明细走返回值+日志，恢复闭环归 06。
- `write_correction` 的「必须在事务内调用」保持 docstring 约定不加运行时守卫：提交 module 不判断连接/事务状态是 04 定下的边界。
- `records/items/state` 参与方命名、`PROFILE_REPLAY_SOURCE_HASH_PREFIX` 名为 PREFIX 实为后缀：均为既有命名，本票不顺手改。

### 7. 全量回归（同跑法：不 deselect、不 ignore、仓外 basetemp、`PYTHONPATH=src` 绝对路径）

| 树 | 失败 | 通过 | 跳过 | 耗时 | 进度字符单写者校验 |
| --- | --- | --- | --- | --- | --- |
| 分支 `f49d1d2`（基线点 7c5d04b + 本票） | 251 | 3860 | 39 | 19:17 | `.`3860+`F`251+`s`39=4150 ✓ |
| 基线 main `7c5d04b`（detached 工作树） | 251 | 3834 | 39 | 19:07 | `.`3834+`F`251+`s`39=4124 ✓ |
| 合并树 `e01a1c4`（本票 + main 并行票 issue-02） | 251 | 3871 | 37 | 17:43 | `.`3871+`F`251+`s`37=4159 ✓ |

- 分支 vs 基线：Δ通过 +26 = 本票 28 条新用例中 26 条双栈 + 2 条仅内存；**失败名单双向 diff 为空**。
- 合并树 vs 分支：**失败名单双向 diff 为空**；Δ收集 +9 = 并行票 issue-02 新用例 7 + 2 条 `NEEDS_WEB_BUILD` 因工作树存在生产构建（并行验收遗留）由跳过转真跑（真跑通过）；Δ跳过 −2、Δ通过 +11，账目闭合。
- 期间事务：main 被并行票 issue-02 推进（`5a75937`+`87e0ea0`），按流程 `git merge main`（`e01a1c4`，零冲突）；两侧对账：合并树 vs main 恰为本票 5 文件（+993/−51）、合并树 vs 分支顶端恰为 issue-02 的 6 文件、改动文件交集为空。
- 教训：一次 pytest 异常中断后复用同一 `--basetemp` 会以同路径残留的 sqlite 库毒化重跑（同用例连续假失败、换干净 basetemp 即过）——中断后必须先删 basetemp。日志与名单留档主仓 `.tmp/issue05/raw/`。

### 8. 未完成与残余风险

- 忘掉的部分来源失败明细仅在 `withdraw_item_sources` 返回值与服务端日志，未接入持久化记账（契约冻结所致）；06 的重启恢复闭环应把「扫描墓碑条目 → 幂等重跑撤回」与该明细一并落地。
- 重放保护只覆盖「重放不得改回」；新消息再陈述同一事实仍按既有证据阶梯覆盖纠正值（1–2 次纠正可被新证据更新，≥3 冻结），这是 v2 既有语义，本票未改。

