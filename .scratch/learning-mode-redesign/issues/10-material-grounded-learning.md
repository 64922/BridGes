# 10 — 照片和文件资料的按需教学

**What to build:** 用户可用现有照片、PDF、DOCX、TXT、Markdown 或本人知识库资料开展学习；导师按问题读取相关部分，说明实际范围，对关键疑点请求补充，并提供真实引用。

**Blocked by:** 03 — 嵌入式理解验证与即时纠错。

**Status:** ready-for-agent

**发布状态：** 用户已确认拆分与依赖，正式发布；尚未实施。

**对应合同：** D02、D05、D06、D09；AC12、AC13、AC16；C01。

## 用户演示

1. 在学习模式上传 PDF 或一张公式照片，说明想理解的内容；纯资料输入也能进入澄清/读取流程。
2. 文件解析中如实显示状态，完成后按当前问题读取相关页/片段，不声称读完整本书。
3. 导师基于已读资料解释并适时检查理解，引用能定位到实际来源。
4. 某个负号看不清时指出位置并请求补拍；其他已支持内容仍能学习。

## 实施范围

1. 复用现有附件上传、解析、图片读取与知识库能力，解除学习模式只允许照片的残余校验；保持现有格式、体积、数量和账户草稿规则。
2. 明确资料学习的当前问题与目的，缺必要目标时只做必要澄清；不将上传整本教材等同于全部已读，不统一要求同书同节。
3. 按目标/问题检索会话资料，知识库仅在当前设置允许且需要时使用。聊天附件不进入全局知识库，不跨会话隐式引用他人或未授权材料。
4. 长资料和多页按预算读取，保存实际采用的页/段/版本。未解析、失败、低质量或未读部分各自有真实状态。
5. 照片文字、公式和图表沿已有登记能力读取；关键符号/单位/定义不清时精确定位疑点，不以模型置信度替代可用依据。
6. 区分照片所见、用户补录、资料原文、模型解释和推导。补录不冒充书页原文，图表推断不当图表数据。
7. 将真实来源与版本接入 03 的学习证据和评价依据；资料修改、补拍或删除后，受影响题目/依据失效，不按过期依据判学情。
8. 外部查证不是本票的前置；资料未覆盖时说明缺口，可给允许的基础解释，需公开核实的内容由 11 接续。

## 接口、持久化与界面

- 上传、解析、读取和引用均校验账户及会话；服务端权限不依赖前端隐藏菜单。
- 保存采用范围、内容版本、原文与推断的来源区别；缓存复用受版本与来源可访问性约束。
- 资料状态、补拍位置和引用在聊天/按需详情可见；解析失败可重试，用户草稿与文字不丢。
- 引用指向实际片段，资料中指令作为数据处理，不能改变工具权限、目标或模式。
- 新材料采用和依据失效数据完成迁移、导出、删除及恢复，原附件生命周期保持一致。

## 范围边界与依赖理由

03 提供真实学习证据与评价绑定，本票将材料来源接入该合同。照片与文件阅读可以在无长期目标时使用，不等待 07。11 独立负责公开来源；不增加音视频学习或新文件类型承诺。

## 验收标准

- [x] 现有支持类型可用于学习，服务端没有统一照片或同小节门禁残留。
- [x] 纯资料可进入正确流程，未解析与未读内容不被声称已读。
- [x] 读取范围遵守当前问题、预算和来源设置，长资料不无边界全量注入。
- [x] 关键疑点只阻塞依赖部分，有准确位置与补充方式。
- [x] 原文、补录、解释与推导来源区分，引用真实可定位。
- [x] 资料版本变化使相关题目/依据正确失效，不改写历史判定。
- [x] 附件/知识库分域、跨账户/会话权限和资料指令隔离成立。
- [x] 上传失败、解析失败、读取中断、补拍和刷新恢复有真实反馈。
- [x] 生命周期、前端构建、合同同步及 C01 审查完成。

## 验证与交付证据

- 覆盖清晰公式照片、关键符号模糊照片、PDF 局部页、讲义和纯文本，捕获实际读取范围。
- 使用资料修改/补拍/删除前后同一题，验证依据版本和失效，不让旧评价串用。
- 覆盖未解析、解析失败、知识库关闭、相关性不足、预算受限和恶意资料指令。
- 运行上传到教学再到引用查看的完整桌面旅程，并验证跨账户、跨会话拒绝。

## Comments

2026-10-13：资料学习切片草稿，待拆分确认。

2026-10-13：用户确认「按此发布」，本票发布到本地问题跟踪器。分诊状态表示信息齐备；须待直接阻塞票验收完成后实施，本轮未开始编码。

2026-10-14：本票在独立分支 `codex/issue-10-material-grounded-learning`（基点 main `bb5d8015`，即依赖票 03 合并后的交付记录）实施并完成验证。

- **门禁解除与准入：** `_validate_draft_attachments` 删除 `photos_only` 参数与 `study_pages_required` 分支，学习模式与日常模式共用同一份附件合同（既有格式/体积/数量/账户草稿规则不变）；`study_payload_forbidden` 文案改为「学习模式只接受文字与上传附件。」；`service_generation_admission`、`service_run_budget`、`service_retry` 调用同步。纯附件消息标题不再一律冒充「照片消息」：新增 `service_helpers._attachment_message_title`（照片占位、文件用首个文件名截断，函数内延迟导入附件模块避免循环导入）。
- **路由（Issue 10 决策）：** 新学习会话（照片或文件）一律进入自适应导师路径 `STUDY_TUTOR_GRAPH_VERSION`（`study-local-tutor-v1`）；附件不再把新会话推入旧书页管线。只有已存在旧 `study_states` 的会话继续留在旧 `STUDY_GRAPH_VERSION`，直到 12 号票旧执行整体退出承接历史。`_study_graph_version` 去掉 `has_attachments` 参数。
- **资料引用（新能力）：** `material_reading.material_source_refs` 把本轮实际读取的照片（当前照片/历史原图）与附件原文片段转成可持久化引用：`kind`（photo/file_segment）、`object_id`、`message_id`、`filename`、`source_version`（内容哈希）、`read_range`（本轮原图/第 N 页/章节）、`ordinal`/`page_number`/`section_title`；只含标识与范围，不含原图字节或正文。
- **提问依据冻结：** `StudyEvaluationBasis.source_refs` 记录提问当时实际采用的引用；`StudyVerificationPlanner.plan` 接收本轮读取引用与「是否携带新材料」，随运行快照冻结（`material_refs`/`stale_check`/`bound_check.source_refs`/`posed_check.source_refs`）；新题依据优先本轮读取，本轮无新读取时沿用在答问题依据（同资料追问不凭空失去来源）。作答证据 `source_refs` 按「冻结依据 + 本轮读取 + 检索引用」合并去重（上限 12），逐条补齐 `available` 可访问标记。
- **失效与补拍（不按过期依据判学情）：** 新增 `StudyUserIntent.MATERIAL_UPDATE`（仅当本轮确实携带新材料且待答问题基于资料时成立，否则按普通意图处理，避免误作废真实作答）。资料删除/不可访问或学生补拍更新时：题目作废、写 `unverified` 证据（带原因与已解析引用）、解除等待、不判对错、不改写历史判定；planner 提示与导师指令同步加入资料教学合同（实际读取范围、来源区分、资料是数据非指令、关键疑点只请补拍依赖部分）。
- **可访问性投影：** `StudyPendingCheckProjection.source_status`（not_applicable/available/unavailable）在读取时解析（`objects.status='active'` 且照片/附件引用另校验 `chat_attachments` 绑定行），不写回历史；前端 `StudyProgress` 在 `unavailable` 时提示「本题依据的资料已不可用或已更新，将按未验证处理，不按旧依据判定」。
- **接口与合同：** `openapi.json` 以最小增量同步（新增 `StudyPendingSourceStatus` schema 与 `source_status` 属性，保留既有键序）；`packages/contracts/src/generated.ts` 用 openapi-typescript 7.13.0 重新生成（仅 +11 行）；合同同步测试通过。
- **持久化与生命周期：** 不新增表/列/迁移（沿用 `study_pending_checks.basis_json`、`study_evidence.source_refs_json` JSON 列），与并发 Issue 05 无迁移编号冲突；删除/导出/恢复沿用 03 已交付的证据生命周期。
- **验收标准证据：** ①`test_files_can_start_and_extend_study_without_photo_gate`（文件起学习、纯附件 201、标题为文件名、运行走 `study-local-tutor-v1`）；②`test_photo_grounded_check_freezes_refs_and_records_availability`（依据冻结、读取范围、`available` 标记、私有 `source_version` 不进投影）；③`test_material_update_voids_old_check_without_judging`（补拍作废旧题、新题依据指向新照片）；④`test_deleted_material_releases_check_without_stale_judgement`（投影 `source_status=unavailable`、作答不按过期依据评价、证据如实记录未验证）；⑤`test_material_source_refs_keep_photo_and_file_read_ranges`（照片/文件来源区分）；⑥既有 `test_v2_17`、`improvement14`、`v2_18/19/20` 回归覆盖读取范围、未读不声称已读、解析失败与刷新恢复。
- **C01 审查：** 导师提示词扩展沿用 02/03 建立的 BridGes 合同基线，规划器提示与 JSON Schema 为本项目原创，未移植参考项目代码或提示词。
