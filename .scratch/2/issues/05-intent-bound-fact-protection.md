# 05 — 按保留意图绑定事实片段，修复盲替换

**What to build:** 原样保留任务保护具体片段，用户授权的纠错和计算正常完成；保护机制不会主动损坏正确内容。

**Blocked by:** 无 — 可立即开始

**Status:** ready-for-human

**优先级：** P0

## 背景与需求

正则恢复反复使用同类第一个候选，能把甲 10 ms 恢复成乙 20 ms；合法纠错和已选合法链接也会被错误覆盖。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/人味化/审查与改进建议.md](../../../docs/人味化/审查与改进建议.md)。
- [docs/人味化/复核脚本.py](../../../docs/人味化/复核脚本.py)。
- [docs/人味化/复核结果.json](../../../docs/人味化/复核结果.json)。
- [docs/adr/0011-clean-room-humanizer-and-license-boundary.md](../../../docs/adr/0011-clean-room-humanizer-and-license-boundary.md)。

## 任务内容

1. 区分原样保留项、允许纠正项、可引用来源和任务计算的新值；只对确有保留意图的具体对象做精确绑定。
2. 精确保留使用稳定 ID/可靠位置与来源哈希或明确逐一配对；已消费目标不能再次配给其他源片段。位置不可靠时拒绝猜测替换。
3. 可用来源清单限定引用资格，不能按清单顺序强制换链接；缺失原片段是否必须出现由任务合同判断，不一律追加到结尾。
4. 裸数字、中文单位、否定、条件、因果及结论强度另列语义参考判断。依靠已有科学/任务核验，不建立声称保证全部事实的正则系统。
5. 关键不一致无法安全修复时采用现有失败/降级语义。修复与提示策略回滚解耦，旧策略快照不能保留确定性代码缺陷。

## 跨票接缝与责任

提供 06 唯一的可绑定片段协议，21 使用保留意图；各模块仍拥有领域证据与科学核验，不能以本票取代它们。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 甲/乙两个同类片段逐一正确绑定，顺序变化不串对象。
- [x] 将 x=1 明确改为 x=2 保留用户授权结果；合法引用 a 不被替换为 b。
- [x] 非必需遗漏片段不被强行补尾，关键缺失与错误按任务分别处理。
- [x] 裸数字、否定、条件和结论强度有明确参考判断，不仅断言字符串存在。
- [x] 净室原创与已退役文章人味化边界保持，修改不新增人味化模型调用。

## 验证与交付证据

重放四个保护区反例，补充多片段/换序/遗漏/纠错/计算场景；在正式普通生成链验证落库事实和任务结果。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## Comments

### 2026-09-30 — 实现摘要（agent，分支 `codex/05-intent-bound-fact-protection`）

- **新增可绑定片段协议** `src/bridges/chat/fact_protection.py`（版本 `intent-bound-fragment-v1`，供 06/21 复用）：把确定性保护区从「按同类第一个候选盲替换」改为按保留意图 + 对象锚点/顺序 + 来源哈希的精确逐一配对。
  - `ProtectionIntent`（原样保留 / 允许纠正）与 `detect_protection_intent`：默认原样保留，命中「改为/改成/计算/换算/求值」等改动指令时按 `CORRECTION` 跳过原样保留，保留用户授权结果与任务计算的新值。
  - `compile_protected_fragments`：识别代码围栏/行内代码/公式/链接/JSON/引用/带单位数值，去重叠，并记录位置、对象锚点（相邻片段之间的标签，如「甲/乙」）与 sha1 来源哈希。
  - `_pair_fragments`：三段式确定性配对（精确 → 锚点一致 → 剩余按顺序逐一配对）；已消费的候选片段不再配给其他源片段，候选盈余时源片段保持未配对并记为不一致（拒绝猜测替换）。
  - `_apply_citation_eligibility`：`additional_sources` 只限定引用资格；候选已合法选中的来源保留，清单外链接才按顺序绑定到未消费的合法来源，无目标时记 `ineligible_citation` 不伪造来源。
  - `assess_semantic_reference`：裸数字、中文单位、否定、条件、因果与结论强度单列语义参考判断（只报告不替换），不建立声称保证全部事实的正则系统。
- **接口/迁移**：`global_writing_policy.restore_protected_regions` 保留同名兼容入口，改为委托协议；`append_missing` 默认由 `True` 改为 `False`（非必需遗漏片段不强行补尾，是否必须出现由任务合同决定）。无数据库/持久状态变更，无需迁移。保护区恢复为确定性纯代码，不随提示策略快照回滚，旧快照重试同样受修复保护（修复与提示回滚解耦）。
- **降级语义**：`turn.py` 的终态落库改用 `plan_fragment_protection`；存在无法安全修复的绑定不一致时，按现有降级语义记 `AuditAction.FACT_PROTECTION`（`DEGRADED`，details 只含意图、协议版本与不一致类别计数，不含正文或片段原文）。流式增量路径沿用同名恢复入口（流式正文/重放一致性属 06）。
- **净室边界**：新增实现、夹具与版本记录均为 BridGes 原创；未引入参考项目可复制资产；保护区恢复不经过模型网关，未新增人味化模型调用（链路级用例断言整轮仅一次 `stream_call`）。退役文章人味化能力保持退役。

**验证（conda `agent`，Windows）：**

- `tests/chat/test_issue05_intent_bound_fact_protection.py`（新增，20 项）：覆盖验收 1–5——多片段/换序/已消费目标不复用、授权纠错、任务计算新值、合法引用不被换、清单外引用绑定、遗漏不补尾/按任务补尾、语义参考判断（裸数字/中文单位/否定/条件/因果/结论强度）、协议版本接缝、嵌套片段去重、普通生成链落库正文且仅一次模型调用。
- 保护区相关既有测试：`tests/chat/test_chat_lightweight_policy.py`、`test_global_writing_policy.py`、`test_expression_fact_drift.py`、新增用例合计 **67 passed / 1 xfailed**。
- `docs/人味化/复核脚本.py` 扩展并重生成 `复核结果.json`：四个反例现显示正确绑定——多片段「甲 10 ms；乙 20 ms」、纠正「改为 `x = 2`。」、跨来源链接保持 a 不被换成 b；纯数字/否定由语义参考判断单列。
- `tests/chat/` 名称级对照：worktree 100 failed / 551 passed / 1 xfailed，`main` 基线 101 failed / 550 passed；逐名比对仅 `test_expression_fact_drift.py::...[study]` 由 failed 变为 xfailed，无新增失败。
- `ruff`：改动文件全部通过（`observability.py` 的 5 处 `UP042` 为既有问题，与本次无关）；`mypy`：新增/改动文件无报错（其余 20 处为既有模块问题）。

**限制与待人工确认：**

- 学习模式辅导走 `qwen_structured_output` 结构化 `invoke` 路径，需要真实书页夹具才会进入被测生成；本票只提供可绑定片段协议，学习链消费由学习票（30–36）接线，故 `test_expression_fact_drift.py` 的 `[study]` 参数改为 `xfail` 并说明边界，**不声称学习模式事实保护已验证**（与《审查与改进建议》局限一致）。
- 流式正文整段伪增量属 06，本票未改；`复核结果.json` 的流式案例仍是模拟。
- 语义参考判断是确定性启发，只作诊断信号，真实模型体验与外部可得性按评测票（39/42）验证。

### 2026-10-01 — 两轴复审加固（agent，提交 `b2acb47`）

`/code-review --base main` 后按两轴意见加固 `fact_protection.py`（仅该文件，85 增 32 删）：

- **类型**：新增 `InconsistencyKind(StrEnum)`，`ProtectionInconsistency.kind` 由裸 `str` 改为枚举，调用方按类别降级不再依赖魔法字符串（与 `ProtectionIntent`/`FragmentKind` 一致）。
- **配对**：第 1 段判等改用 `digest`（与位置/锚点判定口径一致）；第 3 段仅在源/候选两侧数量相等时 `zip(strict=True)` 顺序配对，数量不等即位置不可靠、保持未配对并拒绝猜测替换（原先 `strict=False` 会把盈余源片段静默配到越界候选）。
- **引用资格**：清单外链接仅在恰有一个未消费合法来源可唯一确定时才精确绑定，否则记 `ineligible_citation`，不按清单顺序猜测、也不伪造来源。
- **保留意图按对象判定**：自动判定为 `CORRECTION` 时仅跳过紧邻改动/计算指令的片段（`_CORRECTION_WINDOW=24`），其余片段仍原样保留（此前命中任一指令即整段跳过）；显式传入 `CORRECTION` 仍整段跳过。
- **`semantic_check` 默认开启**（只报告、不替换、不设门）。

**复验（conda `agent`，Windows）：**

- `tests/chat/test_issue05_intent_bound_fact_protection.py` **20 passed**；`test_chat_lightweight_policy.py` + `test_global_writing_policy.py` + `test_expression_fact_drift.py` **47 passed / 1 xfailed**。
- 修复前后 `tests/chat` 失败/错误**名单逐名一致**（327 项，零差异）；与分支点 `d4c16ee` 基线比对：`tests/chat` 仅 `test_expression_fact_drift.py::...[study]` 由 failed 转 xfailed，无新增问题；受影响子集（observability/security/contracts/expression/architecture/storage）33 failed / 210 passed / 2 skipped 与基线**逐名一致**。327 项失败/错误为环境既有（conda 环境缺 SSL CA 文件，`ssl.py load_verify_locations` 抛 `FileNotFoundError`），基线同样复现。
- `docs/人味化/复核脚本.py` 重跑输出与已提交 `复核结果.json` **无差异**。
- `ruff`：改动文件无新增问题（`observability.py` 5 处 `UP042` 为既有）；`mypy`：`fact_protection.py`/`global_writing_policy.py` 无报错（20 处既有问题在 `graph.py`/`service.py` 等未改动文件）。

无新增模型调用，净室边界不变；全量 `pytest -q` 因既有慢用例超时未跑完（受限证据见上）。

### 2026-10-01 — 独立验收与问题修正

本次用户委托独立验收，比较当前 `main=182a734`，通过 `code-review` 两轴复核并修正交付中的实际问题：移除数量相等顺序猜测及单来源换链；修复普通解释/换算、混合纠错、否定与材料内授权词；关键不一致改走可重试错误终态，补齐审计脱敏与任务必需性验证。两轴复审确认已报告阻塞均已处理。

验收定点 **98 passed / 1 xfailed**。完整的实现合同、标准/需求发现、完成的扩大比较及限制见[独立验收记录](../acceptance/05-intent-bound-fact-protection.md)。此前交付段落保留为历史记录，涉及顺序配对、24 字窗口、单来源换链和仅审计降级的描述，以本次验收后的合同为准。

验收结论：本票接受；为 06/21 提供加固后的 `intent-bound-fragment-v1`。学习接线、真实模型评测及流式一致性仍按原消费者工单实施，不以本票冒充这些能力已验证。

### 2026-10-01 — 合并、同步与清理完成

- 验收修复提交 `dcc77dc`，合并提交 `fc1a354`；保留主线同期 Issue 03 的合入内容，自动合并无冲突。
- 合并后 conda `agent` 定点复验 **98 passed / 1 xfailed**，ruff 通过；已推送 `main`，远端核对合并提交一致。
- Issue 05 工作树实体目录、本地 Issue 分支均已删除；已清理并复查失效 worktree 记录，当前仅主工作树登记。
- 收尾记录随主线提交和同步。本次接受仅限本票范围，已知学习/流式/评测边界见[验收记录](../acceptance/05-intent-bound-fact-protection.md)。


