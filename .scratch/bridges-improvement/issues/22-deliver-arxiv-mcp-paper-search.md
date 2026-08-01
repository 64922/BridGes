# 22 — 交付受限内置 arXiv MCP 论文搜索
Status: ready-for-agent
Blocked by: [10](./10-deliver-account-qwen-credentials-and-probes.md), [20](./20-deliver-layered-retrieval-and-citations.md)
Covered requirements: EXT-02, CHAT-07, CHAT-09, BONUS-01, SCORE-02, DESKTOP-01
ADRs: [0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [0010](../../../docs/adr/0010-declarative-skills-and-permissioned-mcp.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0020](../../../docs/adr/0020-layered-retrieval-and-mandatory-teaching-search.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

以 arXiv 论文搜索交付首个受限、只读、默认内置 MCP 纵向切片。两种对话模式都能从“+”菜单选择“论文搜索”，也能在用户自然语言要求搜索某领域论文时调用。返回真实论文链接、元数据、中文简介、与问题的相关依据和后续学习建议，并接入消息引用体系。

该 MCP 固定版本并在受限进程运行，只允许访问登记的 arXiv 网络端点；不得读取文件系统、环境变量、百炼 Key、SMTP 授权码、画像保险库或执行外部命令。查询只包含完成论文检索所需的脱敏主题词。

## Acceptance criteria

- [ ] 日常陪伴和学习模式的同一“+”菜单均显示带原创图标的“论文搜索”入口。
- [ ] 用户以菜单或自然语言提出领域与约束后，界面显示确认后的查询主题和可取消的搜索过程。
- [ ] 每个结果至少包含标题、作者、发布日期、arXiv 标识符、摘要链接或 PDF 链接、中文简介、相关依据和学习建议。
- [ ] 结果链接与 arXiv 返回标识一致；不存在的论文、作者或链接不得由模型补造。
- [ ] 最终回答中的论文主张使用可展开引用卡，并能回到对应 arXiv 页面。
- [ ] MCP 权限清单仅声明必要 arXiv 网络访问，默认拒绝文件、进程、秘密凭据和未登记域名。
- [ ] 传给 arXiv 的查询不含 QQ 邮箱、用户名、私人附件原文、完整画像或秘密凭据。
- [ ] 超时、限流、无结果、响应损坏和 MCP 启动失败显示独立中文错误与重试入口，不以模型记忆替代真实结果。
- [ ] 工具卡具有 loading、empty、error、permission 和 recovery 状态，刷新后保留已完成结果与真实来源。
- [ ] 账户审计记录调用时间、权限、查询数据类别和结果状态，不额外保存敏感上下文正文。

## Verification

- 在 Conda `agent` 环境运行 MCP 权限默认拒绝、进程隔离、查询脱敏、响应解析和引用一致性测试。
- 使用确定性 arXiv 响应覆盖有结果、无结果、超时、损坏响应和取消；禁止测试通过生成虚构论文兜底。
- 运行前端类型检查和桌面 E2E，覆盖两种模式菜单入口、自然语言调用、结果卡、引用跳转、失败及重试。
- 显式运行真实 arXiv 冒烟测试，抽查论文 ID、作者和链接与官方页面一致。

## Non-goals

- 不在本 Issue 建立通用第三方 MCP 安装市场或允许任意权限。
- 不自动下载全部论文进入个人知识库，也不声称对论文全文完成同行评审级判断。
- 不实现或验收移动端布局与交互。

## Blocked by

- [Issue 10：账户级 Qwen 凭据与能力探测](./10-deliver-account-qwen-credentials-and-probes.md)
- [Issue 20：分层本地检索与引用](./20-deliver-layered-retrieval-and-citations.md)

## Comments

arXiv MCP 是后续通用 MCP 治理的最小可信样板；它的内置身份不能绕过权限清单和审计。
