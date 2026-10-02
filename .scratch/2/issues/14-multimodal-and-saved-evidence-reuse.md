# 14 — 统一照片预算与旧附件、原图、证据读取

**What to build:** 长会话添加照片仍受同一预算；追问旧图片、文件或模块细节时按需读取实际原材料并显示真实读取范围。

**Blocked by:** 04 — 守住最终模型载荷预算与材料权威边界；11 — 解析任务指代并补回必要原文

**Status:** ready-for-agent

**优先级：** P1

## 背景与需求

带照片轮直接绕过编译，下一轮旧图细节又没有原图依据；旧助手描述不能替代真实材料读取。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/上下文工程/讨论记录.md](../../../docs/上下文工程/讨论记录.md)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。
- [docs/上下文工程/审查与改进建议.md](../../../docs/上下文工程/审查与改进建议.md)。
- [docs/上下文工程/复核脚本.py](../../../docs/上下文工程/复核脚本.py)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/study-workflow.md](../../../docs/workflow/study-workflow.md)。

## 任务内容

1. 当前照片以实际图片部件和数量/输入方式进入最终预算，不回退到无界完整历史；图状态保存附件引用，不重复储存原图。
2. 依据 11 的具体对象/版本和问题选择性读取同会话原图；问左下角小字等视觉细节必须有实际原图，视觉摘要只用于定位。
3. 文件按对应页码/章节/片段取得已解析原文，补回末尾限定条件；学习书页复用现有来源结构，未解析/不可读状态如实反馈。
4. 已保存模块证据保留结果对象、来源时间、版本和实际取得的摘要/全文范围；仅有摘要不能声称已读全文或原图细节。
5. 删除、失效或不能读取时给具体缺口，并使依赖缓存不可作为当前依据。照片/文件保持账户+会话域，知识库保持账户域。
6. 读取已有材料与刷新外部来源分开；不因用户追问自动执行未被请求的新模块。外部刷新按 12 的已确认意图/约束校验，材料始终是数据。

## 跨票接缝与责任

提供统一对象读取/成本/来源清单给 15、学习和日常节点；13/18 通过依赖失效通知使派生物失效。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 照片轮编译产物与预算不再为 null，文本/图片混合不越最终门。
- [x] 旧图局部问题使用原图或明确无法判断；模型旧描述不被记录为看图依据。
- [x] 附件末尾条件和页码可定位，模块全文/摘要读取范围诚实。
- [x] 越账户/越会话读取被拒绝，删除或失效来源不复用派生缓存。
- [x] 停止、断线恢复和输入预算限制不重复绑定附件或偷偷扩大读取范围。

## 验证与交付证据

用实际附件服务与正式图分派测试照片轮及下一轮追问；模拟原图删除、解析失败、摘要级模块结果和两账户对象。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 执行与验收记录

### 2026-10-02：实现、两轴评审与修复

- 交付分支 `codex/14-multimodal-and-saved-evidence`（工作树 `.worktrees/14-multimodal-and-saved-evidence`，基点 main `c870e194`）；本记录与实现同一提交。
- 环境：Windows + conda `agent`（Python 3.11.15）；确定性替身适配器（`_CapturingAdapter`）只证明机制。
- 读取计划合同 `material-read-v1`（`src/bridges/chat/material_reading.py`）：
  - `plan_photo_reads` 确定性、只读：当前轮绑定照片全部进入；视觉细节追问按「第 N 张」序数或「上一张/那张」定位，历史原图单轮上限 `MAX_REFERENCED_PHOTOS=1`、扫描窗口 `PHOTO_SCAN_MESSAGE_LIMIT=40`；定位不到给具体缺口，不用旧描述顶替。
  - `collect_photo_refs` 逐消息经附件服务的账户+会话作用域列出（类型复用 `PHOTO_MEDIA_TYPES`）；`read_photo_payloads` 载荷时按计划重读一次原图，跨账户/跨会话/已删除/消息不匹配都转缺口；图片部件只进内存，不写入图检查点或持久状态。
  - `select_file_segments` 按页码/章节/末尾（按页序取末尾片段）选已解析整段，命中引用但整段不可读、页码缺失、章节未命中的情况都如实给缺口；`read_evidence_block` 生成实际读取回执与「旧文字描述不构成已读原图/原文依据」边界说明。
- 模块证据读取范围（`src/bridges/chat/evidence_scope.py`）：从 paper/github/tieba/career/resources/arxiv 已保存投影如实提取来源时间、来源消息锚点与实际读取范围（标题/摘要/已读页数/README 状态/深查条数），仅有摘要标注「未通读全文」，链接可得不等于已读全文；由 `_reference_objects_block` 随定位对象注入，不发外部请求。
- 编译接缝（`context_compiler.py`/`service.py`）：照片轮移除旁路（不再 `return None, None`）；`image_count` 按真实图片部件数量以 `IMAGE_COST_TOKENS` 计入统一输入预算；`CompiledTurnContext` 新增 `image_attachment_ids`（计划集合，实际送达以载荷清单为准）与 `material_read_plan`（读计划/缺口/版本）并写入 `CONTEXT_COMPILED` 审计。
- 回合接缝（`turn.py`）：新增 `material_reads` 系统块与逐条脱敏清单回执；`_material_reads` 回执携带 `adopted`，未读到的缺口以 `adopted=False` 逐条入清单（满足调用材料清单「采用与排除原因」合同）；照片轮纯附件/不可读文案与 V2 Issue 05 保持一致；停止/重试按同一编译计划重读，不重复绑定、不扩大范围。
- 附件原文读取（`retrieval/repository.py`、`retrieval/service.py`）：`attachment_original_segments(account, conversation, round)` join 引用/分块/文档/对象，要求文档 `ready`、对象 `active`，账户+会话+轮次三重限定，删除或失效返回空由回合层给缺口。
- 持久化/迁移：本票不新增持久状态、表、路由或迁移；新增字段仅进审计记录；账户/会话/附件与知识库分域、历史可读/导出未动。
- 两轴评审（标准＋规格并行子代理）发现与处置：①回执硬编码 `adopted=True` 且缺口无排除条目 → 改为数据驱动并新增 `adopted=False` 缺口条目；②「附件末尾」无选择逻辑、章节未命中静默回退 → 增加末尾按页序选择与章节未命中缺口；③文件原文读取缺会话过滤 → 查询加 `conversation_id`；④编译记录 `image_attachment_ids` 注释称「实际」但源自计划 → 更正语义并抽 `image_attachment_ids_from_plan`（按 `MaterialReadKind` 派生）；⑤锚点无对象 ID 时空集被当作「不过滤」→ 跳过该锚点的范围注入；⑥证据范围 `version=message_id` 易误读成对象版本 → 更名 `source_message_id`、「来源消息」呈现；⑦清理未用导出（`EVIDENCE_SCOPE_VERSION`/`evidence_scope_lines`/`FileSegment.to_record`/`PhotoRef.position`/`MaterialReadPlan.image_attachment_ids`），常量与类型经枚举复用。未采纳：照片选择未消费 11 的 `ReferenceResolution`（照片不在 11 的文本结果列表锚点里；本票用确定性序数/最近一张且有界，作为已记录限制）；依赖失效通知的消费者实现（票面归 13/18；本票以缺口说明与内容版本回执提供依据，派生缓存按 13/18 的失效机制处理）；回执参数改类型化对象（沿用清单合同的 mapping 形状，跨票接缝不再封装）。另：中间产物 `evidence_scope.py` 一度带 UTF-8 BOM，被 `tests/architecture` 扫描捕获并已修复。
- 验证：
  - 新增 `tests/chat/test_improvement14_material_reads.py` 22 项：计划确定/有界与序数/最近一张/缺口、当前照片编译预算非空且图片成本入预算、旧图原图回读与清单读取范围+内容版本、删除后缺口而非旧描述、跨账户/跨会话读取被拒、文件页码/末尾/章节缺口、模块摘要不声称全文、触底保留请求。全部通过。
  - 定向回归：`tests/chat`（改进 04/11、V2-03、V2-05、照片与附件相关）与 `tests/retrieval` 关键文件共 155 passed。
  - `tests/chat`：分支 100 failed / 791 passed / 1 xfailed；main 同命令失败集逐项一致（100=100），0 新增失败（passed 差额＝新增 22 项）。
  - 非 chat 全量（排除 `tests/integration/test_runtime_smoke.py`）：分支 148 failed / 3547 passed / 39 skipped；main 同范围失败集逐项一致（148=148），0 新增失败。
  - `test_runtime_smoke.py` 在本机 main 与分支上同样挂起（子进程等待、无输出；排除磁盘与残留进程后仍复现），判定为环境性问题，不计入本票对照；其余全量合计失败 248 项与 main 同范围基线一致。
  - `mypy`：改动文件仅剩既有基线错误（`turn.py` 的 `dict.get(str | None)`；行号随插入偏移）；`ruff check` 改动文件全部通过；`py_compile` 通过。
- 限制：仅证明确定性机制；真实模型体验、语义漏召回与外部来源可得性按评测票验证。照片历史定位用确定性的序数/最近一张，不消费 11 的指代解析结果；派生缓存（摘要等）的失效通知由 13/18 的消费者接缝执行，本票提供缺口说明与来源内容版本回执。学习书页复用既有来源结构，未在本票内改动学习摄取。

