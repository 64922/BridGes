# 13 — 学习资料推荐

**What to build:** 用户显式选择学习资料推荐后，可按本轮要学的技术方向收到有顺序、有直达链接的图书与视频清单。

**Blocked by:** 11 — 论文搜索

**Status:** ready-for-agent

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

**编排合同：** 显式 `resources` 进入 `resources.parse → resources.search_books / resources.search_videos → resources.rank → resources.present`。先保留本轮主题、目标和可靠的学习阶段；确实缺少影响推荐的层次时持久化澄清。图书核对书目、ISBN 或出版社信息，视频由公开检索发现后核对可得元数据；只将验证成功的条目送入排序与生成。普通聊天只提供明确的一键建议。

- [ ] 菜单与 chip 可选择资料模块；检索主题保留本轮原始专业名词，学习层次确实影响推荐且上下文不足时只追问这一项。
- [ ] 默认尝试提供两本书和三条哔哩哔哩视频，逐项核对书目或视频可取得的元数据、链接及适用阶段。
- [ ] 展示由浅入深的顺序和选择理由；未看过的视频不描述不可验证的具体内容，条目不足时说明实际数量。
- [ ] 可见真实检索词、来源和失败位置；代表性图书与视频来源经实际可得性验证。
