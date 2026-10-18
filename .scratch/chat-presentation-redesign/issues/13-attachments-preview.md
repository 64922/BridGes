# 13 — 保留附件上传并在对话旁查看真实文件

**What to build:** 用户上传文件或照片、看到真实处理状态，发送后能从消息附件打开可支持的预览或原件，而不离开正在进行的对话。

**Blocked by:** 08 — 从真实引用打开右侧资料并继续对话

**Status:** ready-for-human

> 已于 2026-10-17 经用户确认发布。仅在本票所有阻塞项完成验收后开始；无阻塞票可立即领取。设计与原型已批准，无需重新决定视觉方向。

## 当前事实与动机

用户附件属于会话或发送前账户草稿，不自动进入知识库和画像。原型只演示文件名，正式实现必须沿用真实草稿、解析和访问控制。

## 工作范围

- 将真实附件入口融入新输入区，保持上传、移除、解析与发送绑定流程。
- 在右侧面板接入已有可支持的安全预览；不支持格式提供明确说明和已有下载／打开方式。
- 对照片、多文件及解析状态采用紧凑但可辨识的展示，不把“已上传”误称“已阅读”。

## 验收标准

- [x] 附件绑定原会话和原消息；相同文件名不替代对象身份，不能访问其他账户附件。
- [x] 上传中、上传失败、处理中、可用、空内容与解析失败有明确反馈与真实恢复动作。
- [x] 发送失败不丢可用草稿，移除未发送附件不删除其他消息中的已绑定对象。
- [x] 正文、附件入口、摄取详情和引用页码使用同一对象身份与版本。
- [x] 预览加载失败或格式不支持时可以回到正文，并有合法的原件入口；不默认执行附件中的 HTML 或脚本。
- [x] 切换账户或会话时取消读取、清理敏感预览与临时资源，晚到结果不串页。
- [x] 预览时输入与发送仍可用；关闭预览保留对话阅读位置。
- [x] 保持现行大小／格式限制，不把附件自动写入知识库或画像，不展示虚假的“已解析”。

## 验证方式

真实草稿上传到首轮／后续消息绑定、失败重发、移除、解析错误、权限拒绝与历史预览；格式不支持也要能完整演示降级路径。

每票完成受影响行为的测试与项目要求的检查；涉及前端必须完成生产构建。复用已有可用依赖，不例行重装。外部依赖不可用、既有失败和本票新增失败分别记录，未验证项不得标为通过。最终整合票不能替代本票自身的验证。

## 范围外

不增加 OCR／转换引擎、不承诺所有格式内嵌预览，不新增文件编辑、云同步或附件自动入库。

## 执行约束

遵循已批准的 ADR-0044、设计原则 0005、本功能 PRD 及仓库准则。在 Conda 的 agent 环境开发。优先复用现有接口与领域能力；只有用例证明现有合同不足时才改协议，并在同票完成服务端、生成类型、消费端和测试。无必要不改 schema，不搬迁数据。

本票必须交付可演示或可验证的完整行为，而不是只有接口、组件或截图。内部路径直接替换，不引入兼容 shim、双写或长期双实现。只修改本票需要的范围；不要在执行中顺便重写业务。

## 完成后的交接

提供实际支持格式与降级列表、草稿生命周期和对象访问隔离证据。

交接记录包含实际变更、验证命令和结果、必要截图／服务证据、遗留限制与回退方式；只有行为验收通过才解除后续票的阻塞。

## Comments

- 2026-10-17：依据已验收原型、设计原则及当前代码只读核对形成草案；未开始业务开发。
- 2026-10-17：用户确认「发布」，按已批准拆分转为 ready-for-agent；未开始业务开发。
- 2026-10-18：实现与验证完成，转 ready-for-human；交接见下节。

## 交接记录（2026-10-18）

**分支与基线：** `13-attachments-preview`，基线 main `36832b2d`。前置票 08 已在 main `d17f26a7` 合并并验收。本票只改前端，未触及后端、schema、协议、依赖或数据迁移。

**实际变更**

- 新增 `AttachmentViewerPanel`：默认不渲染；用户点击消息附件后才在右侧读取该对象。面板与对话独立滚动，保留正文与输入区可用；`role="dialog"`、`aria-modal="false"`，打开时聚焦面板，Esc 与关闭按钮均可关闭。
- 图片与 PDF 通过同源内容接口取回 Blob，再用 object URL 展示；卸载、切换附件、关闭面板或切换会话时 `AbortController.abort()` 并 `revokeObjectURL`。PDF 另提示浏览器无法显示时下载原件。
- TXT 用等宽纯文本；Markdown 复用 `MarkdownBody`（`rehype-sanitize`），不执行附件中的 HTML／脚本。文本类只预览前 1 MB，超出时明确提示并保留下载原件。
- 消息附件入口改为可聚焦按钮（`attachment-open-${object_id}`）：照片出缩略图，文档出类型、大小与真实摄取状态；错误／空内容旁保留中文原因、下载原件和重新添加／重新发送说明。
- 页面统一持有附件与引用选择：打开一个即关闭另一个；切换会话清空两者与触发按钮引用；关闭后按“触发按钮 → 输入框 → 主内容区”恢复焦点。选择键包含 `conversation / message / object / content_hash`，账户切换由 `AppShell` 的 `accountRevision` key 重挂载。
- 上传失败现在保留原始 `File`，提示条提供同一文件“重试”，重试成功后生成新草稿；草稿解析失败／空内容提示移除后重新添加可解析版本，不再提供无处理器的假重试按钮。
- `AttachmentIngestionDetail` 仅在传入 `onRetryIngestion` 时渲染“重新解析”；真实后端该端点已退役并返回 410，避免点击后只得到失败。

**实际支持格式与降级**

- 内嵌预览：PNG、JPEG、GIF、WebP 图片；PDF（浏览器内置查看器）；TXT（纯文本）；Markdown（安全渲染）。
- 降级：DOCX 及其他未支持格式明确说明不能内嵌预览，并始终提供“下载原件”；图片／PDF 读取或解码失败、内容为空时显示提示，可重试或下载原件；文本超过 1 MB 时只显示前 1 MB 并提示下载完整原件。
- 不新增 OCR／格式转换；照片只在本会话内预览，不进入全局知识库或画像，不把“已上传”表述为“已阅读”或虚假“已解析”。

**接口与对象身份**

- 复用 `GET /api/chat/conversations/{conversationId}/attachments/{objectId}/content`；前端只新增 `fetchChatAttachmentContent` 包装以支持 `AbortSignal`，后端合同未改。
- 附件身份固定为 `object_id` 与 `content_hash`，同名文件不合并；面板按 `conversation / message / object / content_hash` 重挂载。后端继续按账户与会话校验对象访问，越权返回 404。
- 未改现有 10 MB／格式限制，未绑定任何自动入库行为。

**草稿生命周期与对象隔离证据**

- 发送成功才清空草稿；发送失败保留文字、附件与页序。上传失败只影响当前文件，其他草稿保留；移除未发送附件只调用草稿删除接口，不删除其他消息中的已绑定对象。
- 既有 `tests/chat/test_v2_05_photo_attachments.py`、`tests/chat/test_v2_06_file_attachments.py`、`tests/chat/test_chat_attachments.py`、`tests/chat/test_issue04_atomic_binding.py`、`tests/chat/test_issue04_attachment_contract.py` 覆盖绑定原消息、重复绑定拒绝、跨账户访问拒绝、草稿清理不触碰已绑定对象；本票目标测试 60 通过、1 个既有失败（见下）。
- 新增同名附件单测按 `object_id` 区分；引用详情与附件入口使用同一对象身份，`tests/retrieval/test_retrieval_service.py` 3 项通过。

**验证**

在 `apps/web` 下执行：

- `npm run typecheck`：通过。
- `npm run lint`：通过；仅两处既有 `<img>` 警告（`search-page-client.tsx`、`ImageTaskCard.tsx`）。
- `npm run test:unit`：48 文件 / 393 测试通过。
- `npm run build`：生产构建成功；`.next/standalone` 复制 node_modules 时因工作树 junction 出现 EPERM 警告，构建与路由产物正常。
- `CI=1 BRIDGES_PYTHON=C:\Users\33755\anaconda3\envs\agent\python.exe npx playwright test e2e/issue13-attachments-preview.spec.ts`：4/4 通过（30.1s）。覆盖预览打开时继续发送、关闭焦点与阅读位置、照片／Markdown／DOCX 降级、读取失败重试、快速切换只呈现当前附件、切换会话清除预览，以及 1280×720／1440×900／1920×1080 与 125% 缩放视口。
- 合并后在 main 复跑：`typecheck`、`lint`、393 项单测与生产构建均通过（主仓库依赖布局下无 EPERM 警告）；清理生产构建残留的 `.next` 后 E2E 亦为 4/4 通过（32.2s）。首次复跑因复用生产构建的 `.next` 导致 dev server 退出，清理后稳定，属本地验证环境问题，不涉及产品代码。

后端目标验证（worktree 根目录，`PYTHONPATH` 指向 worktree `src`）：

- `pytest tests/chat/test_issue04_atomic_binding.py tests/chat/test_issue04_attachment_contract.py tests/chat/test_v2_05_photo_attachments.py tests/chat/test_v2_06_file_attachments.py tests/chat/test_chat_attachments.py tests/knowledge_base/test_knowledge_base_service.py -q`：60 通过、1 失败。
- 失败为既有问题：`tests/knowledge_base/test_knowledge_base_service.py::test_chat_attachment_record_not_mutable_via_kb_path`，仍以退役的 positional 参数调用 `ingestion.enqueue`；main 上同样失败，与本票前端改动无关。
- `pytest tests/retrieval/test_retrieval_service.py -q`：3 通过。

**证据**

- `.scratch/chat-presentation-redesign/evidence/13/attachment-panel-1280x720.png`
- `.scratch/chat-presentation-redesign/evidence/13/attachment-panel-1440x900.png`
- `.scratch/chat-presentation-redesign/evidence/13/attachment-panel-1920x1080.png`
- `.scratch/chat-presentation-redesign/evidence/13/attachment-panel-1280x720-zoom125.png`
- 截图显示正文、附件面板与输入区互不遮挡，125% 缩放下对话区仍可滚动。

**遗留限制**

- DOCX 及范围外格式不内嵌预览，按票面要求降级为说明＋下载原件；PDF 能否内嵌取决于浏览器查看器。
- 文本预览上限 1 MB；不做 OCR、转换或全格式渲染。附件仍不自动进入知识库或画像。
- 后端解析重试端点已按 V2 Issue 06 退役（410），本票不复活；解析失败的真实恢复路径是下载核对后在本会话重新发送或移除后重新添加。`e2e/issue17-document-ingestion.spec.ts` 与 main 同一版本，仍断言旧文案「等待解析／已可检索」和该已退役重试按钮；该 spec 在 main 上已与聊天卡片 `CHAT_INGESTION_LABELS` 不一致，本票未修改（非本票范围），建议后续按当前合同重写。
- 账户切换未做独立浏览器用例，由 `AppShell` 的 `accountRevision` 重挂载、面板卸载中止请求／释放 object URL 与单测 abort 覆盖；会话切换、快速切换与迟到响应有 E2E。
- 工作树 junction 导致生产构建出现 EPERM 警告，不影响构建结果；合并后在主仓库常规依赖布局下复验即可消失。

**回退**

- 回退本票前端提交即可恢复旧消息附件展示；无 schema、协议、数据迁移或后端兼容负担。
