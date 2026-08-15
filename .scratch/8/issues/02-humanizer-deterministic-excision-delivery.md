# Issue 02：人味化确定性剔除交付——机械违规剔除后交付成品

Status: ready-for-agent

Type: task

Priority: P0

User stories: US-01

## 已验证现状与根因

用户诉求（《八次改进》#1）：「系统应该把这种拦截的标准应用在生成回复方面，而不是事后拦截。用户让系统人味化润色一段文字时，应该期望得到的是一篇成品回复，而不是拦截警告。」

现状（ADR-0027 设计）：首稿 → 保真硬门 + 契约检查 + 表达审稿 → 失败触发**至多一次**定向修订（`_run_targeted_revision()`，`src/bridges/skills/humanizer/service.py:1134-1286`）→ 修订后重跑全套检查仍 blocking → `status=ERROR`、`error_code="fidelity_gate_conflict"`、`output=None`，用户只看到拦截文案（`service.py:1844-1849`）。写作调用上限 2 次（`service.py:153`），无第三次模型调用。

已冻结决策（本轮 grilling #1）：修订仍失败时，对**机械可剔除**类违规做确定性句子级剔除，重跑校验通过后交付成品并如实标注；真正破坏原文事实（不可机械修复）时才维持拦截。

机械可剔除错误码集合（无来源新增类，剔除即消除风险）：

- `UNATTRIBUTED_CLAIM`（无账本来源的新增可核查项）
- `ASSUMPTION_NOT_ALLOWED`（未授权的假设标注片段）
- `ASSUMPTION_CARRIES_FACT`（假设内承载高风险事实）
- `FIRST_PERSON_UNBOUND`（无来源新增亲历）

不可机械修复、维持拦截的错误码（破坏原文事实类，`_preservation_check()`，`source_ledger.py:731`）：引语/URL/公式/代码/引用被破坏、因果方向反转、否定删除、结论强度升级、亲历丢失等。

### 上下文指针

- `src/bridges/skills/humanizer/service.py:1073-1132`（`_expression_checks`）、:1134-1286（定向修订）、:1740-1870（`_expression_finalize` 终态）、:2568/:2772（旧路径 `_review_checks`/`_finalize_result`）。
- `src/bridges/skills/humanizer/source_ledger.py:621-1017`：硬门与错误码产生点，finding 已携带具体条目与 note。
- `src/bridges/skills/humanizer/revision_policy.py:98-129`：错误码分类先例（`UNSOURCED_CLAIM` 集合）。
- `docs/adr/0027-article-targeted-second-pass.md`：终态语义需补 addendum。
- 前端：`apps/web/src/components/bridges/HumanizerResultCard.tsx:100-101,233-252,287-339`（状态标签/错误详情/保真与修订分区）。
- 既有测试：`tests/humanizer/test_article_revision_service.py:220`（修订后仍破坏事实→停止交付）等。

## What to build

1. **确定性剔除模块**（新文件，如 `src/bridges/skills/humanizer/excision.py`）：输入为最新候选正文 + 硬门 blocking findings；按 finding 定位违规条目所在**句子**并整句剔除；输出剔除后正文与移除清单。纯确定性、零模型调用，不占用写作调用额度。
2. **接入终态流程**：定向修订后重跑检查仍 blocking 时，若全部 blocking findings ∈ 机械可剔除集合 → 执行剔除 → 对剔除稿重跑同一版本全套检查（保真 + 硬约束包含 + 表达审稿）→ 通过则以「已剔除交付」终态交付；`revision_audit.final_state` 新增取值（如 `excised_delivery`），`delivery_note` 如实写明「已移除 N 处无来源/未授权内容」。新旧两条路径（表达契约流程与旧显式 SKILL 流程）语义一致。
3. **安全护栏**：剔除后正文为空、剔除句数占比超阈值（单一常量，默认 40%）、或剔除稿重检仍不通过 → 维持现有停止交付，不交付残稿。
4. **前端如实披露**：`HumanizerResultCard` 对已剔除交付显示说明 pill/分区（移除条数与条目类别），复制等操作正常可用；未交付态展示不变。
5. **ADR 更新**：`docs/adr/0027-article-targeted-second-pass.md` 追加 addendum，记录「机械违规确定性剔除交付」终态语义与安全护栏。

## 非目标

- 不引入第三次写作/修订模型调用（剔除为确定性处理）。
- 不改变硬门规则与错误码定义；不做 span 级（词级）剔除，粒度为整句。
- 不改变 `needs_user_confirmation` 软信号的既有语义。
- 不追溯修复权限映射与数字匹配误判（属 Issue 01，本 issue 复用其结论）。

## Acceptance criteria

- [ ] 修订后仍仅含机械可剔除类 blocking → 剔除 → 重检通过 → 交付成品，审计标注 `excised_delivery` 与移除清单；全程模型写作调用 ≤2 次。
- [ ] findings 中含任一破坏事实类码 → 维持 `fidelity_gate_conflict` 停止交付，与既有测试语义一致。
- [ ] 剔除后为空 / 超阈值 / 重检不通过 → 停止交付，不交付残稿。
- [ ] 前端对已剔除交付显示移除说明；用户真实场景（Transformer 人味化）在 Issue 01 修复后如仍触发硬门，则经本路径拿到成品而非拦截警告。
- [ ] 旧显式 SKILL 路径与新表达契约路径行为一致；ADR-0027 addendum 已合入。

## Test plan

1. 剔除模块单测：四类可剔除码的句子定位与移除、移除清单、阈值护栏、空稿护栏。
2. 服务层：修订失败→剔除→交付（断言无第三次模型调用）；混合码（可剔除+破坏事实）→停止交付；剔除稿重检不通过→停止交付；新旧路径一致性。
3. 投影/前端：`HumanizerResultCard` 已剔除交付展示测试；`tests/chat/test_humanizer_projection_events.py` 回归。
4. 回归：`tests/humanizer/` 全量。

建议回归命令：

```powershell
python -m pytest tests/humanizer tests/chat/test_humanizer_chat.py tests/chat/test_humanizer_projection_events.py -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-r8-issue02
```

## Observability & rollback

- 指标：剔除交付次数、平均移除条数、剔除后仍拦截次数；`revision_audit.final_state` 分布可查。
- 回滚：常量开关（如 `EXCISION_DELIVERY_ENABLED`，置于 `service.py:150` 既有开关旁）关闭即回到 ADR-0027 原终态。

## Blocked by

01（权限映射与数字匹配结论会影响硬门误判率，剔除交付建立在校准后的硬门之上）

## Comments

- 2026-08-15：决策来自本轮 grilling #1（确定性剔除后交付成品）。设计要点：剔除粒度整句、零模型调用、破坏事实类永不剔除。
