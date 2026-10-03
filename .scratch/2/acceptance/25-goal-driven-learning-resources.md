# 工单 25 独立验收

日期：2026-10-03。原交付 `b9fdfa50`（分支 `codex/25-goal-driven-learning-resources`，工作树 `D:\BridGes\.worktrees\25-goal-driven-learning-resources`），基点 `main@806600ce`。开发和验证使用 conda `agent`，Windows、Python 3.11.15。

结论：原交付未完全达标；双轴发现的 3 项规范问题与 6 项规格问题修复并复验后，本票机制验收通过。真实外部来源可得性、真实视频内容质量与完整专业先修关系不属本票确定性逻辑可证明范围。

## 双轴审查

### Standards 轴（初始发现，均已修复）

1. 图书来源、视频核对、图书读取没有逐调用检查停止状态。现已覆盖书源检索前、视频发现前、视频逐页核对前、图书读取前。
2. 视频整批核对只登记一次预算调用，实际却逐页发 HTTP 请求；来源请求超时没有严格受剩余截止时间约束。现改为逐页登记/释放共享预算，每个真实外部调用一项；读取与来源 HTTP timeout 取配置值与剩余 deadline 的较小值。
3. 正文在媒介被跳过或来源未装配时仍声称两路已检索。现改为说明计划查询词与实际请求/跳过记录，不无条件声称两路都已检索。

### Spec 轴（初始发现，均已修复）

1. `language`、`time_budget`、`basis_evidence` 只解析保存，未影响计划和交付。现进入查询条件、默认数量压缩、组织说明与投影。
2. 标题命中加任意简介可能进入主线，无法证明主题和先修适配。现主线必须同时有已读内容主题命中（`content_covered`）、非标题证据与适用性依据（`suitability_basis`）。
3. 系统学习额外主线可能超过明确数量。现额外主线受 `target_books`/`target_videos` 限制。
4. 必要证据缺口没有接入共享的一轮补证预算。现在仅在仍有未读图书时领取一次共享调整额度，最多补读一轮；二次领取被账本拒绝，仍不足只交付候选。
5. Open Library 聚合年份、出版社、ISBN 后直接写成同一版本。现明确标注为来源书目字段，不证明属于同一具体版本。
6. 读取请求固定超时，可能超过共享墙钟预算。现 `deadline_seconds()` 移除 0.5 秒下限，读取与来源请求超时受剩余 deadline 约束。

两个审查轴均未发现额外的 Fowler smell 阻断项。

## 修复内容

配方升级为 `learning-resources-recipe-v2`；解析与计划支持语言、完整时间条件、基础原话、明确数量 0、超过 20、中文十位数量；明确媒介仍是硬边界，另一媒介的矛盾数量不重新启用被排除的检索；短期或每日少量时间只压缩默认数量，明确数量优先，并说明不保证在给定时间内学完；新增 `STAGE_UNKNOWN`、`content_covered`、`suitability_basis`；明确「不适合入门」「需要/要求/先修」等文字保守降为候选；`resources.counts_and_media` 改为必需质量门；书、视频逐真实调用登记与释放共享预算；视频逐页核对共享同一截止时间；停止状态在书源、视频发现、视频核对、图书读取前检查；正文改说明计划查询词与实际请求/跳过；投影补齐 `language`、`time_budget`、`basis_evidence`；新增生命周期、账户隔离、旧配方拒复用与「不联网」父图守卫测试。

本次复核确认 `LearningResourcesProjection(...)` 的 4 个构造路径（kernel 成功投影、service 澄清/停止/错误）均保存三个新增条件字段；另修正 service 错误路径新增字段的缩进。

## 本次验证

使用 `C:\Users\33755\anaconda3\envs\agent\python.exe`（等价于 conda `agent`）。

专项测试：

```
python -m pytest tests/resources tests/kernel tests/commute tests/state_copy -q \
  --basetemp=.tmp/issue25-acceptance-frozen \
  --junitxml=.scratch/25-acceptance-frozen.xml
```

结果：**245 passed in 64.22s**。覆盖三类学习目标、媒介裁剪、明确数量与不足说明、证据分层与标题假阳性、否定先修反例、共享预算与逐页视频调用、停止后不外发、单轮共享补证与二次领取拒绝、SQLite 收据恢复、旧配方隔离、账户导出/删除、「不联网」父图拒绝且来源零调用、资料轮不调用聊天模型也不创建教学计划。

静态检查：

- `python -m ruff check src/bridges/resources src/bridges/kernel src/bridges/state_copy tests/resources tests/kernel`：**All checks passed**。
- `git diff --check`：通过。
- `python -m mypy src/bridges/resources --no-incremental`：分支与 main 均为 **20 errors in 10 files**，逐项对比错误文件与消息完全一致；唯一差异是 `src/bridges/chat/graph.py` 的 6 处 `add_node` 重载错误行号因父图集成增加 6 行而整体 +6（951/952/955/959/963/964 → 957/958/961/965/969/970）。资源模块与内核自身无新增类型错误。

## 全量对比

分支全量：

```
python -m pytest -q --basetemp=.tmp/issue25-full-final \
  --junitxml=.scratch/25-branch-full-final.xml
```

- 分支：**242 failed / 4890 passed / 39 skipped / 2 errors in 1361.84s**。
- main 基线（`D:\BridGes\.scratch\25-main-baseline.xml`，命令同构）：**243 failed / 4843 passed / 39 skipped / 2 errors in 1396.70s**。
- 按测试 ID 逐项对比（5,127 → 5,173 项）：
  - **新增失败（分支失败而 main 通过）：0**。分支失败集合是基线失败集合的严格子集。
  - 修复（main 失败而分支通过）：1，即 `tests.resources.test_resources_module_flow::test_other_modules_still_rejected_and_no_silent_search`。
  - 仅 main 存在：12 项旧 `ranking` 测试（原均通过，随模块重构移除）。
  - 仅分支存在：58 项（新增验收测试与改名/更新用例），状态全部通过，无新增测试失败。
  - 跳过量一致（39 / 39）。
- 基线失败集中在退役入口、旧消费者、CLI 子进程找不到 `bridges`、MCP/插件/运行时环境等既有问题，不以失败总数代替分类；本票以「分支未新增任何基线外失败/错误」判定达标。

## 剩余限制

- 本票确定性逻辑不能证明真实外部来源可得性、真实视频内容质量或完整专业先修关系。
- Open Library 当前读取作品页，不等于读取具体 edition；年份、出版社、ISBN 不保证同版。
- 语言条件进入查询，但来源语言仍需实际核对。
- 时间条件只影响默认数量压缩，不保证用户能在该时间内学完。
- HTTPX timeout 已受剩余 deadline 约束，但仍不能保证持续慢速响应被精确墙钟中断。

临时 XML/log 证据已在提交前清理，本文件保留结论。
