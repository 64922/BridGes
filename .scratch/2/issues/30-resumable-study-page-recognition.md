# 30 — 按页恢复书页识别并定位关键材料疑点

**What to build:** 同节书页按页识别并保存可恢复产物，关键符号不清时询问具体位置，未处理页不被说成已读。

**Blocked by:** 04 — 守住最终模型载荷预算与材料权威边界；09 — 持久化整次运行预算与有限调整额度；10 — 以校园通勤贯通持久节点、收据与执行内核；14 — 统一照片预算与旧附件、原图、证据读取

**Status:** ready-for-agent

**优先级：** P0

## 背景与需求

学习测试夹具在 study_pages_required 阶段被拒，尚未走到事实保护；书页符号疑点和逐页恢复需真实质量门。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/study-workflow.md](../../../docs/workflow/study-workflow.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/人味化/审查与改进建议.md](../../../docs/人味化/审查与改进建议.md)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。

## 任务内容

1. 首轮照片输入要求与单会话单小节保持；纯文本/PDF 不单独启动。合法学习夹具先创建照片、识别/阶段证据，修正被前置门拦截的保真测试。
2. 持久 validate_pages/recognize_page/verify_recognition/request_page_fix，验证附件账户/会话、页序/去重/同节归属与状态。图只存原照片引用，每页产物独立恢复。
3. 记录文字/公式/图表位置、识别路径、原文来源及质量疑点；书页原文与模型解释分字段，图表推断不当原文。
4. 清晰文字先 OCR、公式/图表/复杂排版/疑点视觉核对的优化只在代表样本实测后启用，未验证沿用现有双路径；模型一致/自报置信不是正确保证。
5. 负号/上下标/分子分母/单位/核心定义疑点不能靠置信阈值放行，具体页号位置补拍/补录；文字补录标用户补充。
6. 多页超输入/运行预算时分批保存已完成页，必要页未完成仍待处理；书页内容是数据，不改变模式/工具权限。
7. 材料有效范围提交由领域仓库与内核版本守卫负责，识别失败不更新学生知识判断。

## 跨票接缝与责任

拥有书页识别质量与逐页产物；31 映射范围，35 追加版本，05/06 的学习保真通过本票合法阶段验证。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [ ] 合法学习路径进入真实生成/识别，旧失败夹具原因消除且不绕过书页门。
- [ ] 关键负号/上标不清定位补拍/补录，依赖它的出题阻塞。
- [ ] 多页分批/恢复只重识别未完成或变化页，不宣布整节已读。
- [ ] 用户补录、原文与解释来源区分，页序/归属/账户权限成立。
- [ ] 停止/迟到/版本冲突不覆盖有效材料；OCR 优化有证据才启用。
- [ ] 书页与关键片段版本迁移/导出/删除和正式照片预算可验证。

## 验证与交付证据

覆盖 L01、R02–R03 与合法阶段保真；代表书页 OCR/视觉质量由 42 实测，先保留安全双路径。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## Comments

### 2026-10-03 — 实施与验证记录

**实现说明与接口/迁移变化：**

- `src/bridges/study/kernel.py`（新增）：持久节点 `study.validate_pages` / `study.recognize_page` / `study.verify_recognition` / `study.request_page_fix`，配方 `study-page-validation` / `study-page-recognition` / `study-page-fix`，协议版本 `study-recognition-v2`；质量门 `study.critical_symbols`（关键证据不足走 NEED_INPUT，模型不能自宣通过）。关键符号类别含负号/上下标/分子分母/单位/核心定义，确定性检测要求算式/量值上下文（`well-known`、孤立字母不误判）；模型疑点描述点名关键类别时同样按关键疑点阻塞，不因该位置无片段而降级；双路径核对为去空白的字面包含，高置信不一致仍保持待补充。
- `src/bridges/study/service.py`：识别委托内核；每页产物/收据提交即持久；预算不足保存已完成页并写 `pending_object_ids` 后以 `run_budget_exhausted` 终止；新上传不会静默丢弃上一轮未完成页（保持待处理并提示重试原消息）。用户文字补录标 `source="user"` / `recognition_path="user"`；书页原文与模型解释分字段；照片只保留附件引用。
- `src/bridges/contracts/study.py`：`StudyState.pending_object_ids`；`StudyPage.{object_id, content_hash, model_id, page_number, same_section, replaced_object_ids, unclear, recognition_paths}`；`StudyFragment.{source, recognition_path, interpretation}`；`StudyUnclear.{kind, critical}`。
- 共享接缝：`src/bridges/chat/budget.py` 新增 `load_run_budget`（turn/首轮共用）；`src/bridges/chat/turn.py` 降级学习回答的事实保护保留用户自有 URL 并补齐骨架来源，`run_budget_exhausted` / `run_budget_call_limit` 为可重试码；`src/bridges/ai/model_gateway.py` 预算超时注入抽为 `_payload_with_budget_timeout`（调用方显式单次超时不覆盖，书页图片 180s）；`src/bridges/ai/payload_budget.py` 对无 `messages` 直连载荷按 `image_base64` + `prompt` 计费。
- 迁移：未新增表/列；复用 v65 `node_artifacts` / `node_receipts` / `node_outbox` 生命周期（导出/删除/快照恢复在既有账户清单内），`SCHEMA_VERSION=67` 不变。
- 夹具：`tests/chat/study_state_fixtures.py` 经领域仓库写入合法已识别状态，解除 02/03/08/教学/18/19/画像/chat_service 旧夹具在书页门的拦截，未绕过书页门逻辑。

**验证结果（conda `agent`，Windows）：**

- `tests/chat/test_improvement30_study_pages_recognition.py` **14 passed**：L01 关键疑点按页号+位置补拍/补录并阻塞映射预习；位置级疑点不降级；双路径高置信不一致仍阻塞；用户补录来源标注；原文/解释分字段；R02 收据恢复只补失败页（OCR 调用计数）；预算分批（5 页→4 页保存+1 页 pending，重试各页仅 1 次 OCR）；R03 停止与租约转移迟到提交被拒（`completed` 收据 0）；旧 pending 不被新上传丢弃；书页产物进入导出/删除；照片直连载荷计费。
- 识别全链路 6 文件套件（v2_17 书页 + 识别失败重试 + v2_18/19/20 辅导/复习/小结 + 本票验收）**91 passed**。
- 15 文件回归 **179 passed / 1 failed**，唯一失败为既有退役「学习项目归属」用例（与基线同名）。
- 全量 `tests/chat` **1112 passed / 50 failed**；与基线逐名比对仅 `test_improvement13_acceptance.py::test_121...` 一项差异，该用例在干净 `bd014bd6` 检出同样失败（时间相关既有脆弱用例，非本票改动）；其余 49 项为既有无关失败。
- `tests/storage/test_schema_v65.py` + `tests/kernel/test_node_kernel.py` **19 passed**（导出/删除/快照）。
- `ruff` 改动文件无新增问题；`mypy` 改动源码文件无报错（既有 20 处及 `turn.py:5019` 均位于未改动/既有位置）。
- 限制：夹具直接种已识别状态，不重放照片→识别证据链（该链路由本票验收测试以网关替身覆盖）；网关替身只证明机制，真实 OCR/视觉质量由测评票 42 实测。
- 消费接缝：31 映射、35 版本以本票产物字段为准；05/06 学习保真夹具已经本票合法阶段验证。

