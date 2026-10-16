# 09 — 按明确要求和用途保存敏感自述：验证记录

**分支：** `codex/issue-09-explicit-sensitive-memory`（基点 `26ac16cb`，main）。
**环境：** conda `agent`（Python 3.11.15）；走查为确定性走查（本机规则 + 真实
SQLite 存储 + 真实聊天预处理管线），不调用外部模型、不读取凭据。
**证据：** 本文 + `evidence/demo-walkthrough-20261016.txt` +
`tools/walk_issue09_sensitive_memory_demo.py`（可复跑）。
**可复跑：**

    python .scratch/profile-memory-redesign/validation/09-explicit-sensitive-memory/tools/walk_issue09_sensitive_memory_demo.py \
        --out .scratch/profile-memory-redesign/validation/09-explicit-sensitive-memory/evidence/demo-walkthrough-<stamp>.txt

（走查约 3 秒，运行库在 `.tmp/issue09-demo-*`；脚本只使用临时库，不读取或
记录任何凭据与真实数据。）

## 1. 验收标准对照

| 验收标准（票据） | 证据 |
| --- | --- |
| 心理学兴趣可记录，答辩焦虑默认仅用于会话，不生成长期心理/情绪标签 | `test_issue09_explicit_sensitive_memory.py::test_psychology_interest_remains_ordinary`（`心理(?!学)` 不误拦）；`::test_sensitive_facts_never_enter_automatic_long_term_memory`、`::test_automatic_candidate_gate_ignores_sensitive_values`（自动提交与模型候选两级零写入，覆盖 signals 未收的「过敏」） |
| 明确要求记住的过敏信息用于户外建议，其他问题不携带该内容 | `::test_explicit_allergy_remember_saves_with_purpose_and_gates_adoption`（用途=户外；户外问题采用，计划问题排除原因为「用途与当前任务无关」）；走查 [3] |
| 授权某一项不扩大为全部敏感信息；材料授权也不自动成为敏感授权 | `::test_single_authorized_sensitive_item_does_not_authorize_others`（两个敏感条目各自用途门控）；`commit_fact` 边界（`commit_fact` 是材料/自动写入的公共入口）：敏感与凭据一律返回 None，材料授权不会绕过（代码合同 + `::test_sensitive_facts_never_enter_automatic_long_term_memory`） |
| 密码、验证码和访问密钥不进入记忆及派生存储，即使用户明确要求 | `::test_credentials_are_refused_even_when_explicitly_requested`（3 类凭据：UNRESOLVED + credential；运行记账与 `model_dump_json` 不含明文）；走查 [4] 直查 SQLite 行 |
| 删除或缩小用途后，旧采用及迟到提交均失效，恢复不能扩大范围 | `::test_purpose_clear_invalidates_adoption_and_late_commits`（清除 → 版本 +1、排除原因「敏感自述没有说明用途」、迟到 `commit_fact` 被拒；恢复后仅匹配用途采用）；删除路径沿用 08 墓碑语义（本票未改） |
| 失败不声明已保存，不新增自动记录默认逐条确认流程 | `::test_chat_copy_for_blocked_remember_is_honest`（聊天约束「不得声称已记住」，按原因差异化）；拒绝路径不弹确认、不加逐条授权（无新增交互入口） |

## 2. 确定性走查（最终代码，20261016）

1. **心理学兴趣**：「记住，我对心理学很感兴趣」→ remembered；「推荐几本心理学
   科普书」采用该兴趣。
2. **短期情境**：`commit_fact`「我最近很焦虑」「我对花生过敏」均零写入；抽取器
   提出过敏候选经候选级门禁拦截，条目 0 条。
3. **过敏按用途**：缺用途「记住我对花生过敏」→ unresolved / purpose_required，
   运行记账不含原文，重放仍零写入；「记住我有花粉过敏，户外建议要注意」→
   remembered，用途=户外；户外问题采用、学习计划问题不携带。
4. **凭据**：密码/验证码/访问密钥三例全部 unresolved / credential；画像条目
   0 条；运行记账与数据库行不含明文。
5. **用途收缩**：清除用途（版本 1→2）后户外问题不再采用、迟到提交被拒；恢复
   「户外」后只在户外问题重新采用。
6. **重启与聊天约束**：用途与正文跨重启一致；聊天侧对未保存结果给出「不得
   声称已记住」约束（按 credential / purpose_required 差异化）。

## 3. 自动化测试

- 新增 `tests/profiles/test_issue09_explicit_sensitive_memory.py`（15 项）：
  分类与用途解析纯规则（心理学/密码学不误拦；密码/验证码/密钥分类；用途
  解析与二元关键词匹配；凭据遮罩）；心理学兴趣普通保存与采用；自动路径与
  候选级敏感零写入；明确记住按用途保存与采用门控；缺用途拒绝（幂等重放、
  运行记账不含原文、补充用途后可保存）；3 类凭据拒绝且无明文落库；用途
  清除/恢复与迟到提交；单项授权不扩大；聊天诚实约束；SQLite 重启持久化。
- 新增 `tests/storage/test_schema_v83_profile_usage_purpose.py`（2 项）：v82 →
  v83 补列、旧行 `usage_purpose` 为空、迁移后写入读回、重复初始化幂等。
- 既有测试更新（过敏在工单 09 后属于敏感自述，原「普通事实」用途改中性文本，
  断言语义不变）：`tests/profiles/test_issue03_open_memory_manual_roundtrip.py`
  （「花生过敏」→「打羽毛球」）、`tests/profiles/test_v2_08_atomic_profile_api.py`
  （删除路由用「我喜欢摄影」）、`tests/closeout/test_v2_21_horizontal_regression.py`
  （墓碑防复活用「我养了一只叫团子的猫」）。
- 收口命令 `pytest tests/profiles tests/contracts tests/storage tests/lifecycle`
  → **848 通过**。
- 全量 pytest（xdist `-n 4 --ignore=tests/career_plan`，分支与 main 同命令同参数）：
  - 分支：**327 failed / 5548 passed / 37 skipped**。
  - main：**329 failed / 5529 passed / 37 skipped**。
  - 逐条差集：分支独有失败 **0**；main 独有 2 项为
    `tests/closeout/test_api_boot.py` 环境性 flaky（与 06/08 票记录的同一
    现象一致）。
  - 通过数 +19 = 本票新增测试（15 + 2）+ main 两个 flaky 转通过。
- 前端：`apps/web` `test:unit` 293 通过（34 个文件，含用途展示/清除新用例
  与新增/修改请求的 `usage_purpose` payload 断言更新）、`typecheck` 通过、
  `lint` 通过（仅既有 3 条 warning：两处 `no-img-element`、一处
  `react-hooks/exhaustive-deps`）、`build` 成功。

## 4. 静态检查

- `ruff check`（全部改动文件 + 新增测试文件）：新增 0 问题；`automatic.py`
  13 条 E501 与 `database.py` 1 条 E501 与 main 同文件同项逐条一致（既有
  长 SQL/常量行）。
- `mypy src`：141 errors（main 同命令同为 141，数量一致、无新增；唯一涉及
  改动文件的 `turn.py:5435` 为 main `:5410` 的既有项行号平移）。
- `pip check`：No broken requirements found。

## 5. 接口与实现摘要

- 新模块 `src/bridges/profiles/sensitive.py`（纯规则、无模型）：`MemorySensitivity`
  （ordinary/sensitive/credential）、凭据分类（密码/验证码/访问密钥；`密码学/
  密钥学` 负向断言排除）、敏感领域词表（`心理(?!学)`）、用途解析（显式用途/
  「用于」/逗号场景从句）与规范化（去通用词）、用途匹配（中文长词取二元组、
  英文整词）、`redact_credentials` 值遮罩。
- 契约：`AtomicProfileItem` / 页面投影 / 依据投影 / 创建/修改请求新增
  `usage_purpose`（创建可空；修改不传=不变、空串=清除）；记忆结果新增
  `AtomicProfileMemoryBlockedReason`（credential / purpose_required）并进入
  聊天上下文元数据；抽取结果新增 `SUCCEEDED_MEMORY_UNRESOLVED`。OpenAPI 与
  `packages/contracts/src/generated.ts` 已再生。
- 原子画像：`remember/add_item` 凭据拒绝（`SensitiveMemoryError`）、敏感自述
  用途解析与兜底（显式 → 文本 → 既有条目/墓碑，不扩大）、缺用途拒绝；
  `commit_fact` 敏感/凭据一律零写入（材料与自动写入共用此边界）；采用快照
  新增用途门（无用途不采用 / 用途不匹配排除，普通条目不变量）；任务文本=
  当前问题 + 模块/阶段信息；用途变更 bump 版本并触发撤回通知。
- 自动画像：记忆指令拒绝路径如实返回 UNRESOLVED + 原因（终态、不重试、
  零写入）；被拒消息的运行快照不保留原文；所有运行来源快照出站前做凭据
  值遮罩（`source_hash` 仍按原文）；候选级敏感门禁（在既有 FORBIDDEN 分支
  之后，保持「发明禁止值失败关闭」语义不变）。
- 聊天：`profile_memory_context` 新增 remember/unresolved 文案与按
  `blocked_reason` 的差异化约束（不回显原文，凭据不重复不展示）。
- Web：画像条目展示用途（`atomic-usage-purpose`），添加/编辑表单可填写与
  清除用途（清空即清除，服务端同步停止采用）。
- 存储：迁移 83 `profile_items.usage_purpose TEXT`（旧行 NULL=未说明用途，
  敏感自述不采用）；`REQUIRED_TABLE_COLUMNS` / `MIGRATION_ADDED_COLUMNS`
  同步；SCHEMA_VERSION 82 → 83。

## 6. 剩余限制与边界

- 用途匹配是确定性字面规则（二元关键词包含）：同义改写（「登山」vs「户外」）
  不匹配即不采用，宁可漏用不越界；未做语义相似匹配（后续检索/重整可复用
  同一合同，见票据「交付」）。
- 敏感领域词表是维护成本明确的本地清单：信号分类（signals）与本表各自
  演进；未收录的新敏感类别按普通事实处理（自动路径仍有 signals 兜底），
  新增类别需同时更新本表与测试。
- 明确记住之外的「聊天更正」路径（`correct_fact`）不重新要求用途：它只改写
  既有条目，条目的用途与采用门控不变；跨类别的更正要重新走用途约束。
- 模型侧对「未保存时不得声称已记住」的遵从属提示词约束，未做真实模型评测
  （与 08 票同一限制）；确定性部分（结果状态、上下文文案、零写入）已覆盖。
- 「材料授权不自动成为敏感授权」按公共写入边界（`commit_fact`）编码与测试；
  材料接入票（10+）接入时须继续使用该边界并发起自己的走查。
