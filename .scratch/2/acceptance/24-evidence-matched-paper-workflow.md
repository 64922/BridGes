# Issue 24 独立验收记录

日期：2026-10-03。原交付 `ac9248ad`（前置修复 `949d50ab`），验收基线 `main=806600ce99240a15cf4f8915c55d3325068a70ee`，分支 `codex/24-evidence-matched-paper-workflow`，工作树 `.worktrees/24-evidence-matched-paper-workflow`。用户授权验收修复、合并 main、经现有网络代理推送、删除本票工作树和本地分支，并要求保留 Issue 25/26 工作树。

## 验收结论

原交付存在验收阻塞，不能直接合并。独立两轴审查确认 7 项阻塞并全部修复后，验收通过；确定性条件与验证结果见下节。真实模型语义体验与真实全文可得性仍按 42 评测，本票只以确定性替身证明机制。

## Spec 轴：7 项阻塞与修复

1. **同义证据端到端被概述/交付门挡回。** `cover_original_phrase` 只见字面原词与 `match_basis`，合法同义候选在筛选通过后又被服务层判不覆盖。现在带逐字证据的候选（`match_evidence` 非空）视为已覆盖原目标；新增服务级回归（`test_service_cover_original_phrase_accepts_synonym_evidence`）与生产网关全链路回归证明「federated learning」候选不命中字面「联邦学习」也能交付。
2. **指定论文与来源硬条件未解析、会被替换或静默调用。** 解析器新增裸 arXiv ID 与引号标题识别，以及英文来源提示（without/exclude/avoid/only/from）；筛选在检索前按来源/指定身份过滤（`EXCLUDED_SOURCE`/`EXCLUDED_SPECIFIED`），指定论文或标题时禁用查询调整且不得由其他论文替代（`_violates_specification`）；元数据补充只调用允许来源（`_allowed_metadata_sources`），被排除来源完全不发起外部调用。空结果按硬条件如实标注，不静默放宽。
3. **生产 judge/reviewer 未装配。** `PaperSearchService` 新增 `gateway` 与 `assessment_manifest_sink`；`_resolve_assessors` 复用登记网关、父图同一 `RunBudget` 持久账本、`RunModelQuota` 与材料清单审计（scope `paper.assess`）构造 `PaperEvidenceRole`，每批候选一次批量判断；注入接缝优先。全链路回归走 API + 生产装配路径验证 judge 调用与逐字证据交付。
4. **概述可保留无正文依据的自由文本。** 采取「明确绑定逐字来源证据」路线（未把概述移入 verify）：概述 schema 必含 `evidence_source`（title/abstract）与 `evidence_quote`，无法在真实标题/摘要中逐字定位、超长或未知标识的条目一律丢弃；命中的概述写入 `summary_evidence` 持久绑定。系统提示同时禁止无正文依据的方法/实验/局限/复现/性能断言。
5. **独立复核触发与裁决不健壮。** 比较请求由解析的阅读目标（`GOAL_COMPARE`）或上下文本轮比较诉求触发；来源冲突照旧触发。reviewer 裁决非 Mapping 或缺 `passed` 按未通过处理并阻塞；未装配或裁决无效时追加「独立复核未执行（未装配或裁决无效）」如实标注，不冒充通过。
6. **一轮调整语义未对齐。** 计划固定产出「精确 + 一次调整」两轮；调整只在相关候选不足时发生，改同义查询/主词，绝不更换来源、放宽年份/领域或替换指定论文；检索载荷记录 `attempts`（查询/状态/候选数/相关数/是否采用）、`adjustment`（是否使用/原因/结果）与预算账本开始/结束，可追溯。
7. **恢复可能复用失效结果。** 参考日期取生成运行快照的冻结日期（`prior_digest` 用日期而非仅年份）；检索节点键去掉 `run_id`（按计划、解析哈希、查询与能力），同会话同日新一轮复用有效检索产物，次日重新解析并失效旧结果；`paper.enrich` 依赖检索与解析两端哈希，解析变化即失效。三者均有恢复回归覆盖。

## Standards 轴

本次修复的 ruff（`src/bridges/paper`、`tests/paper`）全过；`mypy src/bridges/paper` 的本票文件 0 错误，其余 20 条与验收基线逐条一致（chat/video/image/runtime 既有，行号因插入位移）。`git diff --check` 无空白错误（仅 Windows CRLF 转换提示）。审查中另修正两处类型收窄（筛选单判接缝的 `hasattr` 守卫、硬条件标题校验的 `None` 收窄），不改变行为。

## 合同与生命周期

`PaperConstraints` 新增 `arxiv_id`/`paper_title`/`allowed_sources`/`excluded_sources`；`PaperRecommendation` 新增 `summary_evidence` 并补 `source_abstract`/`read_evidence`。概述调用仍承载 Issue 21 表达策略与 04 最终预算，材料清单按 `paper.assess` 独立审计。无新增表/列，沿用既有内核收据/产物版本化恢复；旧结果可读导出能力未改动。

## 验证

环境：Windows，conda `agent`，`PYTHONIOENCODING=utf-8`，各命令串行、独立 `--basetemp`（`conda run` 并发临时文件会冲突）。

- `tests/paper`：**85 passed**（原交付时 54 项；本次新增 `test_paper_issue24_acceptance.py` 31 项，覆盖上述 7 项阻塞）。
- 关联回归 `tests/paper + tests/kernel + tests/storage/test_schema_v65.py + tests/commute + tests/resources + tests/tieba + tests/github`：**384 passed / 4 failed**，证据见 `validation/24-integration.xml`。
- 在验收基线 `main=806600ce` 用同一命令独立复跑：**324 passed / 19 failed**——本票修复其中 15 项论文链路失败，剩余 4 项（resources/tieba/github 的既有模块拒绝与建议断言）在 main 同名同因复现，本票 0 新增失败。
- `ruff check src/bridges/paper tests/paper`：全过。
- `mypy src/bridges/paper`：本票文件 0 错误；其余 20 条与 main 基线规范化后逐条一致。
- 未重跑全仓 `pytest -q`（约 23 分钟）；原交付记录的修复前分支全量 228 failed / 4872 passed / 39 skipped / 2 errors（原始快照 `validation/24-issue-before.xml`）与 main 全量 243 failed / 4843 passed 的对照保留为历史证据，本次未复核全量。
- 外部模型/外部来源均为确定性替身，只证明机制；真实语义判断与全文可得性按 42 验收。

## 合并与清理

合并前通过命令级 `http.proxy` 配置 fetch origin 并核对远端（`origin/main=3153fe25`）。最终合并提交、推送结果、远端一致性、工作树/分支删除与 Issue 25/26 保留结果在完成后追加。

### 执行完成（2026-10-03）

- 合并时 main 已由并行会话推进到 `3153fe25`（Issue 26 合入）；本票合并提交为 `f8325cad`。随后并行会话又合入 Issue 25（`61811e82`），实际顺序：`3153fe25` → `f8325cad` → `61811e82` → `f11f7567`。
- 自动合并暴露一处语义冲突：本票将 `api/main.py` 的 `_commute_task_reference` 重命名为 `_current_task_reference`，而 Issue 25/26 新增的 github/resources 调用者仍引用旧名，合并树出现未定义名称（F821；ruff 复核 114→112）。合并后修复 `f11f7567` 统一改用 `_current_task_reference`；资源模块「未接入模块显式拒绝」回归恢复通过（合入前 main 为失败），github/resources 任务版本引用不再在请求期抛 NameError。
- 合并树回归（HEAD `f11f7567`）：`tests/paper + tests/chat/test_improvement15_* + tests/kernel + tests/storage/test_schema_v65.py + tests/commute + tests/resources + tests/tieba + tests/github` = **538 passed / 3 failed**；3 项（tieba 拒绝断言、tieba 建议、github 无模块不启动）与合入前 main `3153fe25` 同名同因复现，0 新增失败。
- 提交：验收修复 `c010a718`、合并 `f8325cad`、合并后修复 `f11f7567`。已经现有代理推送 origin/main，本地与远端一致为 `f11f7567421928249890064365f638597260ef3a`。
- 工作树清理：先 `git worktree remove D:/BridGes/.worktrees/24-evidence-matched-paper-workflow`，再 `git branch -d codex/24-evidence-matched-paper-workflow`，成功；临时校验工作树已移除；`git worktree prune` 无失效项（dry-run 无输出）。Issue 25 工作树与分支保留，`.worktrees/26-github-requirement-evidence-matrix` 目录保留未动。
- 本节为交付后的文档补记，随独立文档提交同步远端；没有再修改产品代码。
