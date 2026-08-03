# 17 — 交付文档摄取与版本化全文/向量索引
Status: ready-for-human
Blocked by: [10](./10-deliver-account-qwen-credentials-and-probes.md), [16](./16-deliver-secure-chat-attachments.md)
Covered requirements: KNOW-01, MODEL-01, MODEL-02, IMP-02, IMP-03, DESKTOP-01
ADRs: [0006](../../../docs/adr/0006-one-model-snapshot-per-capability.md), [0008](../../../docs/adr/0008-contract-locked-embedding-alias.md), [0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0020](../../../docs/adr/0020-layered-retrieval-and-mandatory-teaching-search.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

把安全对象转换为可追溯、可恢复的本地检索材料。支持 PDF、DOCX、TXT、Markdown 和常见图片；提取文本、标题、页码或章节等结构后进行哈希分块，同时写入 SQLite FTS/BM25 与 `text-embedding-v4` 1024 维向量索引。图片优先保留原图与元数据，必要时调用已探测可用的核心多模态模型理解，不额外引入 OCR 模型。

索引必须具有不可混写的版本合同，至少锁定模型 ID、维度、规范化、分块器、解析器和 Schema。合同变化时构建新索引，验证完成后原子切换；旧索引在明确清理前仍可回滚。

## Acceptance criteria

- [x] PDF、DOCX、TXT、Markdown 与常见图片都进入同一持久化摄取状态机，并保留原始对象。
- [x] 文档记录解析器版本、内容哈希、标题、页码/章节和失败原因；分块可追溯到原文范围。
- [x] 相同账户内相同内容可复用解析结果但不混淆来源；不同账户即使哈希相同也不能互见。
- [x] 全文索引与向量索引均可持久恢复；向量固定使用 `text-embedding-v4`、1024 维和已确认规范化合同。
- [x] 当前索引版本绝不接受不同模型、维度、分块或 Schema 的混合写入。
- [x] 合同变化会创建新版本、完整重建、校验覆盖率和维度，再原子切换；失败时继续使用上一可用版本。
- [x] 解析、Embedding 或索引失败保留原文件和明确中文原因，可从失败阶段安全重试且不产生重复分块。
- [x] 缺少或未通过 Embedding 能力探测时显示“向量索引不可用”，不得以 Stub 或空向量标记成功；全文解析仍可独立完成。
- [x] 附件详情显示 loading、queued、processing、ready、empty、error、permission 和 recovery 状态，不以空列表掩盖失败。
- [x] 后台执行器重启后恢复未完成任务，重复领取保持幂等。

## Verification

- 在 Conda `agent` 环境用固定样本文档运行解析、页码/章节、哈希分块、幂等重试和账户隔离测试。
- 运行索引合同测试，覆盖维度错误、版本漂移、重建失败、原子切换与旧版回滚。
- 以确定性 Embedding 假服务验证编排，以显式真实冒烟测试验证账户级 `text-embedding-v4` 能力且不保存 Key。
- 运行前端类型检查和桌面 E2E，核对处理进度、失败原因、重试及重启恢复。

## Non-goals

- 不在本 Issue 实现跨来源融合排序、联网搜索或统一搜索页面。
- 不引入单独 OCR、重排模型或用户可选的 Embedding 模型。
- 不实现或验收移动端布局与交互。

## Blocked by

- [Issue 10：账户级 Qwen 凭据与能力探测](./10-deliver-account-qwen-credentials-and-probes.md)
- [Issue 16：安全聊天附件](./16-deliver-secure-chat-attachments.md)

## Comments

索引版本是派生数据边界，不是用户资料版本。切换成功后才允许新版本服务检索请求。

本次交付：Schema v7 新增摄取状态机（document_records/document_parse_cache/document_chunks）与版本化索引（index_versions/index_active/index_vectors/fts_chunks trigram），存量附件按支持类型回填入队；解析器覆盖 PDF(PyMuPDF)/DOCX(XML)/TXT/Markdown/常见图片（元数据，不引入 OCR）；哈希分块携带原文偏移/页码/章节；版本合同锁定模型/维度/L2 规范化/分块器/Schema，合同变化全量重建、校验覆盖率与维度后原子切换，旧版可回滚；Embedding 按账户探测门调用真实 text-embedding-v4，缺失时全文独立完成；worker 每轮摄取（租约恢复、自动重试上限、孤立清理）；附件卡片显示 8 态芯片与可展开详情、失败重试；新增摄取详情/重试/索引状态 API 与确定性假服务编排测试、显式真实冒烟脚本。验证：1254 pytest（+47 新增，含 3 条审查回归）、117 E2E（+4）、全量 mypy/ruff 干净、npm typecheck/build 通过。

双轴代码审查修复（8 处）：领取饥饿（最旧 limit 份不可领取时饿死后续文档）、永久失败无限自动重试（上限 3 次，手动重试重置）、迁移回填混入不支持类型、重试残留陈旧分块（重试前清理派生行并同步版本计数）、API 索引状态恒报"未启用"（改只读真实版本链）、purge 后版本计数失真、enqueue/mark_retry 未包事务、前端 permission/loading 状态无产出路径；另修复既有 initialize() 升级路径 UNIQUE 冲突（版本行覆盖）。
