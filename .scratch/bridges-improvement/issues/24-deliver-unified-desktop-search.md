# 24 — 交付跨内容统一桌面搜索
Status: delivered
Blocked by: [15](./15-deliver-recent-conversations-lifecycle.md), [17](./17-deliver-document-ingestion-and-versioned-index.md), [18](./18-deliver-local-knowledge-base-page.md), [19](./19-deliver-folder-learning-projects.md)
Covered requirements: NAV-02, NAV-05, KNOW-01, PROJ-01, CHAT-06, DESKTOP-01
ADRs: [0001](../../../docs/adr/0001-chat-first-product-surface.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

交付侧边栏“搜索”入口和完整统一桌面搜索体验，覆盖最近聊天、生成或上传图片、文档及学习项目。搜索标题、消息正文、文档名称与解析文本、图片说明和项目名称；提供类型、项目和时间筛选，显示高亮片段，并能精确跳转到对应消息、图片、文档位置或项目详情。

搜索只处理当前账户的本地持久化内容，不借此调用联网搜索。索引更新、删除和权限变化必须反映到结果，加载失败不得伪装成“没有结果”。

## Acceptance criteria

- [x] 点击侧边栏“搜索”打开遵循 ChatGPT 参考交互的 BridGes 原创桌面搜索页或命令面板，并可关闭返回原上下文。
- [x] 单次查询能返回聊天、图片、文档和学习项目四类结果，结果以可访问文字和原创图标区分类型。
- [x] 支持按内容类型、学习项目和时间范围组合筛选，并提供清除筛选操作。
- [x] 结果高亮真实命中片段；空查询、无结果和索引尚未就绪具有不同中文状态。
- [x] 点击聊天结果精确跳到相应消息，图片结果打开对应资产，文档结果定位到详情及页码/章节，项目结果进入项目详情。
- [x] 最近对话的置顶、改名、删除以及项目移动后，搜索结果及时更新且不保留失效跳转。
- [x] 只返回当前账户有权读取的结果；猜测查询参数或直接打开其他账户结果均安全拒绝。
- [x] 键盘可打开搜索、输入、切换筛选、上下选择、回车跳转和 Esc 关闭，焦点关闭后归还原触发点。
- [x] 页面具有中文 loading、empty、error、permission 和 recovery 状态；错误重试保留查询与筛选条件。
- [x] 刷新和服务重启后可搜索持久内容，不依赖进程内缓存或硬编码演示数据。

## Verification

- 在 Conda `agent` 环境运行四类内容索引、筛选组合、账户隔离、删除同步和精确目标解析测试。
- 运行前端类型检查，并以桌面 E2E 覆盖键盘打开、四类搜索、筛选、高亮、跳转、无结果和错误重试。
- 使用两个账户的同名会话、文件与项目确认查询结果完全隔离。
- 重启 `BridGes start` 后重复查询，并核对消息、页码/章节和项目跳转仍准确。

## Non-goals

- 不从统一搜索入口执行 DuckDuckGo 或 arXiv 联网搜索。
- 不在本 Issue 实现自然语言问答或跨账户共享搜索。
- 不实现或验收移动端布局与交互。

## Blocked by

- [Issue 15：最近对话生命周期](./15-deliver-recent-conversations-lifecycle.md)
- [Issue 17：文档摄取与版本化索引](./17-deliver-document-ingestion-and-versioned-index.md)
- [Issue 18：本地知识库页面](./18-deliver-local-knowledge-base-page.md)
- [Issue 19：文件夹式学习项目](./19-deliver-folder-learning-projects.md)

## Comments

图片检索可基于用户文件名、提示、生成元数据和已有说明；不得为了本地搜索而静默把图片发送到外部服务。
