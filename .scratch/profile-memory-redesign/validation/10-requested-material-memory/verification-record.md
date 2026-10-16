# 10 — 从明确指定的材料更新个人记忆：验证记录

**分支：** `codex/issue-10-requested-material-memory`（基点 `fff98a9d`，main）。
**环境：** conda `agent`（Python 3.11.15）；走查为确定性走查（真实 SQLite
存储 + 真实材料读取组合 `_designated_material_reader` + 真实来源核验
`_material_source_checker` + 真实对象存储，仅模型网关为脚本化替身），
不调用外部模型、不读取真实数据。
**证据：** 本文 + `evidence/demo-walkthrough-20261016T134757Z.txt` +
`tools/walk_issue10_material_memory_demo.py`（可复跑）。
**可复跑：**

    python "<本目录>/tools/walk_issue10_material_memory_demo.py" \
        --out "<本目录>/evidence/demo-walkthrough-<stamp>.txt"

（走查约 10 秒，运行库在 `.tmp/issue10-demo-*`；脚本只使用临时库。）

## 1. 验收标准对照

| 验收标准（票据） | 证据 |
| --- | --- |
| 普通上传、阅读和任务分析不新增个人记忆；明确指定后才启动受控处理 | `test_issue10_requested_material_memory.py::test_ordinary_upload_or_read_is_not_a_profile_update`（附件在场、三条普通消息零运行零读取零模型调用）；`::test_plain_background_request_without_material_is_not_a_material_directive`；走查 [1] |
| 本人可读材料形成有依据事实，不把他人简历或多人内容归给用户 | `::test_worker_extracts_only_verified_quotes_from_material`（引用必须唯一命中材料片段、normalized 必须是引用子串、material_ref 必须已知）；`::test_third_party_material_is_refused_without_reading`（零读取）；`::test_multiple_subjects_in_material_are_refused_without_model_call`；`::test_other_subject_extraction_result_is_reported_uncertain`；走查 [2][3][4] |
| 指定材料不自动授权其中全部敏感内容，凭据继续禁止保存 | `::test_sensitive_and_credential_candidates_are_not_saved`（材料候选仍走 `commit_fact` 敏感/凭据边界）；`::test_material_quote_is_redacted_before_storage`（片段落库前按普通证据同一遮敏口径）；`::test_material_embedded_instructions_cannot_expand_extraction_rules`（材料内伪造授权/指令不改变提取规则，凭据候选零写入）；走查 [2] 采用边界与 [4] 零写入 |
| 未完成解析或只读取部分时，结果如实说明，不伪造页码或引用 | `::test_pending_parse_waits_bounded_then_reports_parse_timeout`（12 次有界等待 → parsing 超时）；`::test_unreadable_and_failed_materials_are_reported_as_is`；`::test_missing_material_is_reported_honestly`；走查 [4] |
| 跨账户拒绝，跨会话附件遵循原访问边界，草稿不成为来源 | 读取经属主仓库（`AttachmentRepository.rows_for_message` 账户作用域 + `objects.status='active'` + 文档分块账户作用域）；走查 [7]（另一账户读不到条目与材料）；草稿（`message_id IS NULL`）不在 `rows_for_message` 命中范围 |
| 材料编辑、删除、撤回或权限失效后，旧候选和派生采用失效 | `::test_commit_recheck_drops_submissions_after_material_edit`（提交前复核，内容版本变化 → 零写入 + `material_invalidated`）；`::test_material_sources_project_evidence_with_locator_and_status`（投影隐藏片段 + 采用排除）；走查 [5] |
| 当前支持的文本文件和图片路径均有实际端到端证明及错误恢复 | 走查 [2]（文件分块路径）与 [6]（照片整图 OCR 路径，qwen_ocr → 提取）；错误恢复 [4]（等待/超时/为空/失败/多人/主体不明） |

## 2. 确定性走查（最终代码，20261016T133305Z）

1. **普通使用**：绑定简历的消息下，「看看这份简历」「帮我分析一下这份成绩单」
   「我上传了简历」均不构成画像指令：零运行、零任务、零材料读取、零模型调用。
2. **本人简历**：明确指令 → run=pending/material、material=processing、聊天约束
   「不得声称已经记住材料内容」；后台处理完成后写入 1 条事实，来源=对象 +
   第 2 页 · 教育经历 + 原话；运行 outcome=succeeded_written、source=qwen_model；
   同轮不采用（「本轮刚整理，下一轮才使用」），下一轮采用。
3. **拒绝与如实**：无材料 → material_missing（零读取）；室友材料 → not_owned
   （零读取、零模型调用、零任务）。
4. **不可用如实**：解析未完成有界等待 12 次后报告超时；解析为空 → unreadable；
   解析失败 → parse_failed；多人材料 → multiple_subjects（零模型调用）；模型
   判定主体非本人 → subject_uncertain（零写入）。
5. **编辑/删除失效**：替换材料版本后旧片段不再展示（source_unavailable）、
   派生采用排除（「材料来源已删除或权限失效，暂不用于回答」）、条目保留；
   撤回后核验 DELETED；重新绑定原材料后恢复展示与采用。
6. **照片材料**：qwen_ocr 整图识别 → qwen_profile_extraction 提取；事实来源
   定位「整图」，media_type=image/png；OCR 未产生运行锁时失败关闭（零写入、
   如实记录 `profile_extraction_material_read_failed`）。
7. **重启与跨账户**：run_kind=material 与材料来源（对象/定位/原话）跨重启保留；
   另一账户看不到条目与材料。

## 3. 自动化测试

- 新增 `tests/profiles/test_issue10_requested_material_memory.py`（17 项）：
  指令识别与普通消息零画像；处理登记与同轮/下一轮采用；引用核验与来源
  投影；提交前复核；敏感/凭据边界与片段遮敏；材料内伪造指令不扩大提取
  规则；第三人称零读取；无材料如实；有界等待超时；不可读/失败/多人/
  主体不明如实；结果元数据往返；SQLite run_kind 与 source_materials 跨重启。
- 定向回归 `pytest tests/profiles`：分支新增 17 项全部通过（存量通过数同步
  增长）；`pytest tests/profiles tests/storage tests/chat`：分支
  **94 failed / 1967 passed**，main 同命令 **94 failed / 1950 passed**，
  失败集逐条比对完全一致（94 项均为预存 chat/MCP/selections 失败），
  通过数 +17 = 本票新增测试。
- 契约同步：`pytest tests/api tests/contracts tests/closeout/test_api_boot.py`
  → **67 通过**（含 OpenAPI 同步）；`openapi.json` 与
  `packages/contracts/src/generated.ts` 已再生（仅新增材料来源投影字段）。

## 4. 静态检查

- `ruff check`（全部改动文件 + 新增测试 + 走查脚本）：新增 0 问题；
  `automatic.py` 13 条 E501、`database.py` 1 条 E501、`commit.py` 1 条 F401
  与 main 同文件同项逐条一致（既有长 SQL/常量行与既有未用导入）。
- `mypy`（全部改动文件 + 新增测试）：仅剩 `turn.py` 1 条既有 arg-type
  （main 同项，行号平移），本票代码 0 新增。

## 5. 接口与实现摘要

- 新模块 `src/bridges/profiles/materials.py`：材料更新指令解析
  （`parse_material_update_directive`：本人/他人归属、材料名词、更新意图、
  疑问句与「背景知识」排除）、就绪折算（`material_readiness`：ready/pending/
  unreadable/failed，未知不当作可读）、定位（页码/章节或「整图」）、多人
  检测（`detect_multiple_subjects`）、引用唯一命中（`segment_for_quote`）、
  读取端口 `DesignatedMaterialReader` 协议、常量与稳定错误码。
- 契约：`AtomicProfileMaterialStatus/BlockedReason/Result`、
  `AtomicProfileMaterialSource`（对象/消息/会话/页序/文件名/类型/内容版本/
  定位/原话）、`AtomicProfileMaterialEvidence` 与 `AtomicProfileItem.
  source_materials`、证据投影 `material_sources`；抽取契约新增
  `ProfileExtractionKind`、`SUCCEEDED_MATERIAL_UNRESOLVED`、材料输出模型与
  `profile-material-extraction-v1` 版本（进入合同版本集合）、
  `ProfileExtractionRun.run_kind`。
- 自动画像：同步路径识别材料指令并登记 `run_kind=material` 的 PENDING 任务
  （登记前零读取零模型；他人/缺失即时终态）；工作器 `_run_material_attempt`：
  就绪等待（12 次）→ 读取（文件分块/照片 OCR）→ 多人守卫 → 材料提取（合同
  版本、锁证据、系统提示词声明材料为不可信数据）→ 引用核验 → 提交前重读
  材料复核（失效条目丢弃，`material_invalidated`）→ 写入（`source=qwen_model`）；
  存储片段与普通证据同一遮敏口径（联系人/密钥样式不落库）；材料运行与普通
  运行共用幂等键，普通调度复用不重置。
- 原子画像：`commit_fact`/`_merge_fact` 贯通材料来源（按对象+消息+页序合并，
  新版本替换旧坐标）；条目持久化 `source_materials_json`；证据投影按核验
  状态展示/隐藏片段；采用门：全部来源失效按「已删除/无法核验」排除，不删除
  条目，用户编辑条目豁免。
- 组合根：`_designated_material_reader`（附件→对象→文档分块/OCR，当前消息
  优先、有界回看，草稿与其他账户不可见；OCR 经既有能力常量与提示词，缺
  运行锁或落锁失败与既有 OCR 端口同一失败关闭口径）、
  `_material_source_checker`（绑定仍在 + 对象活跃 + 内容版本一致；错误按
  无法核验）；`AutomaticProfileService.material_reader` 与
  `set_material_source_checker` 注入。
- 聊天：`profile_memory_context` 支持 `memory_kind=material`（processing /
  unresolved 按 blocked_reason 差异化约束，不声称已记住）；`profile_memory`
  元数据持久化沿用同一键。
- 存储：迁移 85 `profile_items.source_materials_json`（默认 `'[]'`）与
  `profile_extraction_runs.run_kind`（默认 `'ordinary'`）；
  `REQUIRED_TABLE_COLUMNS` / `MIGRATION_ADDED_COLUMNS` 同步；
  SCHEMA_VERSION 84 → 85。

## 6. 剩余限制与边界

- 模型侧「材料内容只作候选、不得凭文件名猜测、材料内指令不得改变规则」
  属提示词与候选核验约束：提示词已声明材料为不可信数据，材料内伪造授权/
  指令的确定性边界（引用核验 + 敏感/凭据门）有测试，但真实模型对提示的
  遵从仍未评测（与 08/09 同一限制）；确定性部分（零写入、引用核验、
  状态如实）已覆盖。
- 前文回看窗口为 3 条消息（`MATERIAL_LOOKBACK_MESSAGES`），更早的材料需在
  指令消息中重新携带；超窗口材料不猜测。
- 解析等待按「有界重试次数」计（12 次），实际等待时长随驱动节拍（生产
  约每 0.5 秒一次）变化；超时后运行终态记录稳定原因，用户侧仅能通过
  画像页查看后续结果，不做主动通知（沿用既有后台任务形态）。
- 照片路径只取第一版整图 OCR 文本，未做区域坐标（定位为「整图」）；
  多页图片材料按多附图逐张处理。
- 材料运行复用普通抽取的幂等键与后端队列，重试上限为材料专用 12 次；
  存量普通运行不受影响。
- 跨会话材料访问遵循附件原有账户+会话边界；知识库（非聊天附件）来源
  不在本票范围（不做自动迁移）。
