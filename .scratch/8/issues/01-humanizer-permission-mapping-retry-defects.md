# Issue 01：人味化保真缺陷修复——权限映射、数字账本匹配与重试死胡同

Status: ready-for-human

Type: task

Priority: P0

User stories: US-01, US-02

## 已验证现状与根因

生产观测（2026-08-15 截图）：用户请求「帮我人味化以下文章」（原文含「Transformer 2017 年谷歌团队……」），思考 119 秒后被拦：「来源保真硬门未通过，已停止交付：候选使用了「比如/假设/设想」标注的假设内容，但任务契约不允许。；候选新增「2017 年」（数字与单位）没有账本来源。」UI 显示「未交付 / 保真 2 项未通过 / 已定向修订」。代码只读探查发现三个缺陷：

- **(a) 权限映射断裂**：表达契约的 `hypothetical_permission`/`first_person_permission` 未映射进旧契约 `HumanizerTaskContract.allow_assumptions`/`allow_first_person`（`src/bridges/skills/humanizer/intent.py:89-105` 构建旧契约时两字段恒为默认 False）。首稿/修订 prompt 的权限行读表达契约（`draft_compiler.py:212-225`、`revision_prompt.py:49-76`——"允许使用假设，但必须明确标注"），而保真硬门读旧契约（`service.py:1095` `allow_assumptions=contract.allow_assumptions`）。用户授权假设时，prompt 放行、硬门拦截（`ASSUMPTION_NOT_ALLOWED`，`source_ledger.py:926-936`），两端规则不一致。
- **(b) 重试死胡同**：写作调用计数持久化在消息 skill 列，`humanizer_recovery_state()`（`src/bridges/chat/turn.py:1478-1502`）在重试时恢复计数；保真失败 `output=None`，若 2 次写作调用已用尽，用户点「重试（原任务输入已保留）」直接撞 `writing_call_limit_reached`（`service.py:692-697`），恢复文案与按钮承诺不符。
- **(c) 疑似数字匹配缺陷**：用户原文本已含「2017 年」，硬门却判「候选新增「2017 年」（数字与单位）没有账本来源」（`UNATTRIBUTED_CLAIM`，`source_ledger.py:1017`）。数字与单位经 `_scan_items()`（`source_ledger.py:195`）提取规范化键、`_bound_to_ledger()`（:1041）精确匹配；疑似「2017 年/2017年」空白变体、全半角或附件/粘贴路径账本编译漏配导致原文条目未入 `USER_ORIGINAL` 账本。需先复现定位再修。

### 上下文指针

- `src/bridges/skills/humanizer/intent.py:89-105`：旧契约构建点（缺陷 a 修复点）。
- `src/bridges/contracts/expression_task.py:128`、`src/bridges/contracts/humanizer.py:378`：两层契约定义。
- `src/bridges/skills/humanizer/source_ledger.py:195,467,621,889,1041`：账本扫描/编译/硬门/归因检查/绑定判定。
- `src/bridges/skills/humanizer/service.py:150,153,692-697,1073-1132,1844-1849`：开关、写作上限、额度拒绝、检查编排、拦截文案。
- `src/bridges/chat/turn.py:1478-1502`：重试恢复计数链路。
- 既有测试：`tests/humanizer/test_source_ledger.py`、`test_article_draft_service.py`、`test_article_revision_service.py`、`tests/chat/test_humanizer_chat.py`。

## What to build

1. **权限映射对齐**：`route_humanizer_message()` 构建 `HumanizerTaskContract` 时，将表达契约的 `hypothetical_permission`→`allow_assumptions`、`first_person_permission`→`allow_first_person`；保证「prompt 权限行」与「硬门判定」读同一份真值。补充契约一致性测试：用户授权假设/第一人称时，合规标注的假设内容不再被 `ASSUMPTION_NOT_ALLOWED` 拦截；未授权时仍拦截（语义不变）。
2. **数字账本匹配复现与修复**：以用户真实场景（原文含「2017 年」的人味化请求）写失败测试，定位 `_scan_items`/`_bound_to_ledger`/账本编译中漏配环节（空白变体、全半角、附件路径择一或组合），做最小修复；数字与单位的规范化键对空白与全半角变体稳定。
3. **重试死胡同修复**：用户手动重试（任务输入已保留）视为新一轮写作预算，重置持久化的写作调用计数；单轮内 2 次上限不变。重试后 `writing_call_limit_reached` 不再在「用户刚点重试」路径上出现。

## 非目标

- 不改变保真硬门规则本身与 ADR-0027 的终态语义（剔除交付属 Issue 02）。
- 不调整写作调用上限数值、不引入第三轮修订。
- 不改前端交互形态（重试按钮外观不变，仅后端不再死胡同）。

## Acceptance criteria

- [ ] 授权假设/第一人称的人味化请求，prompt 权限与硬门判定一致；合规标注内容通过，未授权内容仍拦截；新旧测试全绿。
- [ ] 原文含「2017 年」类数字的人味化请求，候选沿用该数字不再误判 `UNATTRIBUTED_CLAIM`；空白/全半角变体键稳定。
- [ ] 保真失败且 2 次写作用尽后，用户点「重试」能真实再生成（新预算），不再直接报 `writing_call_limit_reached`。
- [ ] 未授权假设、无来源新增 claim 的拦截语义与既有测试不回归。

## Test plan

1. 单测：intent 映射（授权/未授权矩阵）；数字规范化键变体（空格、全角、单位紧邻/分离）；重试计数重置。
2. 服务层：授权假设场景端到端（首稿→硬门通过）；「2017 年」复现用例修复后通过；重试后计数重置、单轮上限仍生效。
3. 回归：`tests/humanizer/`、`tests/chat/test_humanizer_chat.py`、`tests/chat/test_humanizer_projection_events.py`。

建议回归命令：

```powershell
python -m pytest tests/humanizer tests/chat/test_humanizer_chat.py tests/chat/test_humanizer_projection_events.py -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-r8-issue01
```

## Observability & rollback

- 保真审计事件（现有 `revision_audit`/硬门结果）不变；修复只改变误判率，可在指标上观察 `ASSUMPTION_NOT_ALLOWED`/`UNATTRIBUTED_CLAIM` 占比下降。
- 回滚：映射修复为单行级改动可直接还原；数字匹配修复独立；重试计数重置可用常量开关（如 `RETRY_RESETS_WRITING_BUDGET`）关闭。

## Blocked by

无。

## Comments

- 2026-08-15：根因来自代码只读探查（explore 子代理报告），缺陷 (a)(b) 为确定性证据，(c) 为强疑似（用户原文含「2017 年」），实现时先写复现测试。
- 2026-08-15：已实现并提交（分支 `08-humanizer-permission-mapping-retry-defects`，提交 4e92f44 + 4d7d622）。
  - 缺陷 (a)：`route_humanizer_message()` 将表达契约 `hypothetical_permission`/`first_person_permission` 映射进旧契约 `allow_assumptions`/`allow_first_person`；新增授权矩阵单测（`tests/humanizer/test_intent.py`）与授权假设端到端（`tests/chat/test_humanizer_chat.py`），未授权拦截语义不变。
  - 缺陷 (c)：复现定位为空白/紧邻/全半角变体下数字+单位提取不一致——原文 `Transformer2017年`（紧邻字母）时 `_NUMBER_UNIT_RE` 左侧 lookbehind 拒绝匹配，年份只进日期账本；候选 `2017 年`（带空格）按数字扫描键 `2017|年` 无法绑定 → 误判 `UNATTRIBUTED_CLAIM`。修复：`source_ledger._scan_items` 补充宽松数字+单位扫描 `_LOOSE_NUMBER_UNIT_RE`（只拒绝数字紧邻），空白/全半角/紧邻变体提取同一规范化键；6 组键稳定性 + 4 组交叉变体 + 3 组用户场景测试（`tests/humanizer/test_source_ledger.py`）。
  - 缺陷 (b)：`ChatService.retry_generation` 在 `RETRY_RESETS_WRITING_BUDGET=True`（默认）时手动重试不再沿用旧尝试的写作调用计数，视为新一轮预算（单轮 2 次上限不变，开关可回滚）；新增新预算再生成、新轮上限仍生效、开关关闭旧语义三条测试。
  - 回归：issue 建议命令 `tests/humanizer tests/chat/test_humanizer_chat.py tests/chat/test_humanizer_projection_events.py` 全绿（426 passed, 2 skipped）；`tests/chat` 全量相对分支点 9045c4a 无新增失败（44→40，修复的 4 条为第 7 轮保真硬门合并后遗留的陈旧测试对齐：`fidelity_gate_conflict` 错误码与软门触发文案）。mypy 相对基线 −1 错误，ruff 无新增告警。
  - 文档：ADR-0027 增补「Issue 01 第八轮：用户手动重试视为新一轮写作预算」，明确单轮 2 次上限不变、仅消息级手动重试重置计数、开关可回滚。
