# 16 — GitHub 项目推荐

**What to build:** 用户显式选择 GitHub 项目推荐后，可找到与完整 idea 功能接近的公开项目，并理解每项推荐的覆盖范围与局限。

**Blocked by:** 11 — 论文搜索

**Status:** ready-for-agent

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

**编排合同：** 显式 `github` 进入 `github.parse → github.search → github.inspect → github.rank → github.present`。解析保留完整 idea 的用户场景与必要功能；先查整体项目，缺少时再查组件并记录覆盖范围。检查节点区分 API 元数据、README 自述、许可证和实际读取的实现文件，排序优先看功能匹配。限流采用有限缓存与退避，达到额度即保留可重试状态。普通聊天只提供明确的一键建议。

- [ ] 菜单与 chip 可选择 GitHub 模块；提取用户 idea 的核心场景和功能，优先搜索整体相似项目。
- [ ] 默认尝试给出两三个仓库，逐项展示直达链接、功能匹配、借鉴角度、维护与许可证据；组件项目明确标出只覆盖哪一部分。
- [ ] README 仅作为项目自述；未读取实现文件不作内部架构断言，未见许可证不声称代码可自由复用。
- [ ] 限流、文件缺失或匹配项目不足时展示实际结果和局限；完整项目与组件两类输入经过真实 API 验证。
- [ ] 同一日常会话可在论文搜索后切到 GitHub，用“找实现它的项目”指向可追溯的前文原词；移除 chip 后普通聊天不误启动 GitHub。
