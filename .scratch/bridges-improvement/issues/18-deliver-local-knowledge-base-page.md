# 18 — 交付全局本地知识库桌面页面
Status: ready-for-human
Blocked by: [04](./04-establish-desktop-design-baseline-and-brand-assets.md), [12](./12-deliver-chatgpt-desktop-shell-sidebar.md), [17](./17-deliver-document-ingestion-and-versioned-index.md)
Covered requirements: NAV-02, KNOW-01, IMP-03, DESKTOP-01
ADRs: [0001](../../../docs/adr/0001-chat-first-product-surface.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0020](../../../docs/adr/0020-layered-retrieval-and-mandatory-teaching-search.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

用完整可操作的“本地知识库”桌面页面代替旧文件库。用户可以上传全局笔记和学习资料，查看解析及索引状态，打开详情，重试失败任务，重建索引并删除材料。已就绪材料作为当前账户的全局文件，可被任何获准对话检索，但不会自动共享给其他账户或外部服务。

页面沿用已确认的 ChatGPT 式桌面外壳和 BridGes 原创视觉基线，具备明确返回新聊天路径。不得出现“功能将在这里呈现”、假进度或只有标题的空壳。

## Acceptance criteria

- [x] 侧边栏“本地知识库”进入完整桌面页面，并可通过 Logo 或明确按钮返回新聊天。
- [x] 用户能上传 PDF、DOCX、TXT、Markdown 和常见图片，并看到逐文件上传、解析、全文索引与向量索引状态。
- [x] 列表和详情显示名称、类型、大小、更新时间、来源、内容哈希摘要、索引版本及可用于对话的状态。
- [x] 失败项显示实际阶段和中文原因，可重试解析或索引；成功项可以显式触发版本化重建。
- [x] 删除前说明影响，确认后删除对象、分块和派生索引；正在被任务使用时给出一致且可恢复的处理结果。
- [x] 全局材料默认仅对当前账户可见；所有列表、详情、下载、重试、重建和删除操作均校验账户归属。
- [x] 页面具有 loading、首次使用 empty、筛选无结果 empty、error、permission 和 recovery 状态，并保留用户已完成的选择。
- [x] Embedding Key 不可用时准确显示全文可用/向量不可用的降级状态，不把知识库整体伪装为已就绪。
- [x] 所有按钮连接真实 API，刷新、服务重启和账户切换后状态一致且不串号。
- [x] 中文桌面布局支持键盘操作、清晰焦点、确认对话框 Esc 关闭和长文件名无横向页面溢出。

## Verification

- 在 Conda `agent` 环境运行知识库仓库、对象/索引级联删除、任务重试、重建和账户隔离测试。
- 运行前端类型检查，并以桌面 E2E 覆盖首次空态、上传、处理中、成功、失败重试、删除和账户切换。
- 重启 `BridGes start` 后核对材料、状态和索引版本均恢复。
- 人工检查页面不存在占位文字、硬编码进度或无返回路径。

## Non-goals

- 不在本 Issue 实现学习项目文件夹、跨类型统一搜索或联网搜索。
- 不允许知识库材料跨账户共享。
- 不实现或验收移动端布局与交互。

## Blocked by

- [Issue 04：桌面设计基线与品牌资产](./04-establish-desktop-design-baseline-and-brand-assets.md)
- [Issue 12：ChatGPT 式桌面外壳与侧边栏](./12-deliver-chatgpt-desktop-shell-sidebar.md)
- [Issue 17：文档摄取与版本化索引](./17-deliver-document-ingestion-and-versioned-index.md)

## Comments

“本地知识库”中的“本地”指 BridGes 单机数据边界；模型调用是否使用某段材料仍受最小披露和用户授权控制。

本次交付：Schema v8 将 document_records.conversation_id 改为可空并新增 source（chat_attachment/knowledge_base）与 rebuild_requested，迁移保留全部存量行与子表数据；新增 /knowledge-base 路由（上传/列表/详情/下载/重试/重建/删除，全程账户作用域、统一 404），上传沿用原始字节+文件名头约定与 10MB 上限，同名同内容幂等复用；IngestionService 支持 conversation_id=None 的全局材料，rebuild_material 清除派生数据后重新入队并由 worker 产出真实新索引版本（旧版可回滚），delete_material 事务内级联清除分块/FTS/向量/解析缓存后走 pending_cleanup 删对象，处理中一律 409 中文可恢复提示；前端以完整客户端页面替换占位空态：逐文件 XHR 真实进度上传、状态芯片轮询、行内元数据（类型/来源/哈希摘要/索引版本/可用于对话）、失败阶段+中文原因、详情/删除/重建对话框（Esc 可关、说明影响）、筛选与文件名搜索（无结果独立空态、选择跨刷新保留）、向量降级页面级横幅+行级徽标、首次空态含返回新聊天。验证：1267 pytest（+21 新增；4 个 runtime_smoke 失败经 HEAD 对照确认为预置环境问题）、mypy strict 干净、ruff 改动文件干净、openapi 契约同步、npm typecheck/build 通过、全量 131 E2E 中 127 通过（issue18 9/9；issue04/issue08/issue12 各 1 个失败经 HEAD worktree 对照确认为预置失败，issue17:286 单跑通过属并发抖动）；真实后端+worker 冒烟验证上传→处理→就绪→全进程重启后材料/状态/索引版本完全一致（向量降级如实呈现）。

双轴代码审查修复（10 处）：rebuild/delete 租约检查移入事务消除 TOCTOU 竞态、delete_object 失败不再误报 404（改 503 可重试中文提示）、_material_record 增加 source 过滤使对话附件无法经 KB 路径变更（纵深防御）、_material_index_context 匿名四元组改 NamedTuple、跨模块私有导入提升为公共名（validate_filename/sniff_media_type/SUPPORTED_MEDIA_TYPES）、KB 上传头幂等语义对齐对话附件并写入契约文档、前端列表补齐类型/来源/哈希摘要（AC3）与失败阶段（AC4）、删除未使用的 getKnowledgeBaseMaterial、上传 XHR 助手提取共享、删除/重建确认对话框合并为 ConfirmActionDialog。
