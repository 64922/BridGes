# BridGes 执行 Issue 清单

> 状态：已获用户批准，等待代码代理按依赖认领执行。
>
> 主计划：[`BridGes项目改进计划书.md`](BridGes项目改进计划书.md)
>
> 本地 PRD：[`.scratch/bridges-improvement/PRD.md`](.scratch/bridges-improvement/PRD.md)
>
> 历史隔离：不得修改、认领或更新 `.scratch/science-companion-plan/` 与根目录 `tickets.md`。

## 执行规则

- 先读取本 Issue 正文、全部 blocker、相关 ADR 和 `CONTEXT.md`，再修改代码。
- 除 Issue 01 外，任务默认 `ready-for-agent`；代理开始时按仓库约定更新状态。
- 每个 Issue 都是可演示的纵向切片，完成时必须同时交付数据合同、真实 API、电脑端界面、错误恢复和自动测试。
- 禁止用占位页、硬编码状态、无 handler 按钮或生产 Stub 假成功满足验收。
- 产品只面向电脑端：不做手机、平板、移动浏览器、PWA、移动抽屉或触屏专用布局。
- 前端结构以 `https://chatgpt.com/` 电脑端为模板，美术采用 Claude-inspired 风格，但 Logo、图标和组件资产必须原创。
- Conda `agent` 是当前开发验证环境；用户部署同时支持自行创建 Conda/`.venv` 环境和 Docker/Podman。

## 任务清单

| ID | Status | Issue | Blocked by | 交付结果 |
| --- | --- | --- | --- | --- |
| 01 | `ready-for-human` | [轮换疑似泄露百炼 Key 并封存旧原型](.scratch/bridges-improvement/issues/01-rotate-compromised-key-and-freeze-legacy.md) | — | 旧 Key 失效，旧库只读封存 |
| 02 | `ready-for-agent` | [建立独立测试状态与稳定基线](.scratch/bridges-improvement/issues/02-stabilize-isolated-test-baseline.md) | — | 测试不依赖执行顺序或共享状态 |
| 03 | `ready-for-agent` | [切换 BridGes 品牌并移除 `.env` 配置](.scratch/bridges-improvement/issues/03-rename-bridges-and-remove-env-config.md) | 02 | 包名、CLI、配置合同完成 expand 阶段 |
| 04 | `ready-for-agent` | [建立电脑端设计基线与原创品牌资产](.scratch/bridges-improvement/issues/04-establish-desktop-design-baseline-and-brand-assets.md) | 03 | ChatGPT 结构基线、Claude-inspired 令牌、Logo 与全模块图标 |
| 05 | `ready-for-agent` | [建立全新 SQLite 与加密对象存储](.scratch/bridges-improvement/issues/05-build-clean-sqlite-and-object-storage.md) | 02 | `bridges.db`、迁移、对象库和账户隔离 |
| 06 | `ready-for-agent` | [交付源码与容器统一运行时](.scratch/bridges-improvement/issues/06-deliver-unified-source-and-container-runtime.md) | 03, 05 | Conda/`.venv`、Compose 与 `BridGes start` |
| 07 | `ready-for-agent` | [交付 QQ 邮箱与用户名注册登录页面](.scratch/bridges-improvement/issues/07-deliver-qq-username-auth-pages.md) | 04, 05, 06 | 完整登录/注册页面及安全 Cookie |
| 08 | `ready-for-agent` | [交付底部账户菜单与个人资料设置](.scratch/bridges-improvement/issues/08-deliver-account-menu-and-personal-settings.md) | 04, 07 | 四项上拉菜单、头像、用户名和退出路径 |
| 09 | `ready-for-agent` | [交付设备账户切换与敏感操作再认证](.scratch/bridges-improvement/issues/09-deliver-device-account-switching-and-reauth.md) | 08 | 有效会话切换、当前/全部退出、近期验密 |
| 10 | `ready-for-agent` | [交付账户级 Qwen 凭据与能力探测](.scratch/bridges-improvement/issues/10-deliver-account-qwen-credentials-and-probes.md) | 05, 07 | 固定模型逐项真实探测，无 Stub 或隐藏降级 |
| 11 | `ready-for-agent` | [交付持久化流式聊天](.scratch/bridges-improvement/issues/11-deliver-persisted-streaming-chat.md) | 05, 07, 10 | 真实 Qwen 对话、保存、恢复和重试 |
| 12 | `ready-for-agent` | [按 ChatGPT 电脑端基线交付全局外壳](.scratch/bridges-improvement/issues/12-deliver-chatgpt-desktop-shell-sidebar.md) | 04, 08, 11 | 固定顺序侧栏、Logo 新聊天、折叠、无“更多” |
| 13 | `ready-for-agent` | [交付新聊天输入区与空白态](.scratch/bridges-improvement/issues/13-deliver-new-chat-composer-and-blank-state.md) | 04, 11, 12 | 五条名言、消息操作、菜单外壳、三张建议卡及功能删减 |
| 14 | `ready-for-agent` | [交付对话模式与思考摘要](.scratch/bridges-improvement/issues/14-deliver-conversation-modes-and-thinking-summary.md) | 11, 13 | 双模式、角色差异和截图约定的展开/折叠 |
| 15 | `ready-for-agent` | [交付最近对话生命周期](.scratch/bridges-improvement/issues/15-deliver-recent-conversations-lifecycle.md) | 11, 12, 14 | 两种模式最近、置顶、改名、删除和跳转 |
| 16 | `ready-for-agent` | [交付安全聊天附件](.scratch/bridges-improvement/issues/16-deliver-secure-chat-attachments.md) | 05, 11, 13 | 文件/图片上传、归属、下载和删除 |
| 17 | `ready-for-agent` | [交付文档摄取与版本化索引](.scratch/bridges-improvement/issues/17-deliver-document-ingestion-and-versioned-index.md) | 10, 16 | 解析、分块、FTS/Embedding 索引与重建 |
| 18 | `ready-for-agent` | [交付本地知识库页面](.scratch/bridges-improvement/issues/18-deliver-local-knowledge-base-page.md) | 04, 12, 17 | 全局材料上传、状态、重试、删除和重建 |
| 19 | `ready-for-agent` | [交付文件夹式学习项目](.scratch/bridges-improvement/issues/19-deliver-folder-learning-projects.md) | 12, 15, 16 | 对话与项目文件组织，不恢复项目制教学 |
| 20 | `ready-for-agent` | [交付分层检索与引用](.scratch/bridges-improvement/issues/20-deliver-layered-retrieval-and-citations.md) | 14, 17, 19 | 本地来源融合、配额、页码/章节引用 |
| 21 | `ready-for-agent` | [交付 DuckDuckGo 隐私联网搜索](.scratch/bridges-improvement/issues/21-deliver-duckduckgo-private-web-search.md) | 20 | 脱敏查询、可见过程和网页引用 |
| 22 | `ready-for-agent` | [以 arXiv 交付首个受限 MCP](.scratch/bridges-improvement/issues/22-deliver-arxiv-mcp-paper-search.md) | 10, 20 | 论文链接、简介、依据与学习建议 |
| 23 | `ready-for-agent` | [交付学习模式教学证据门](.scratch/bridges-improvement/issues/23-deliver-learning-mode-teaching-gate.md) | 14, 20, 21, 22 | 因材施教、测验和本地不足强制联网 |
| 24 | `ready-for-agent` | [交付电脑端统一搜索](.scratch/bridges-improvement/issues/24-deliver-unified-desktop-search.md) | 15, 17, 18, 19 | 搜索聊天、图片、文档和项目并精确跳转 |
| 25 | `ready-for-agent` | [交付数字分身画像中心与静态头像](.scratch/bridges-improvement/issues/25-deliver-profile-center-and-static-avatar.md) | 04, 07, 16 | 九类画像、证据、时间、治理和头像 |
| 26 | `ready-for-agent` | [交付画像候选与许可更新](.scratch/bridges-improvement/issues/26-deliver-profile-candidates-and-permissioned-updates.md) | 11, 25 | 明确记住、低风险自动写入、敏感确认和情境信号 |
| 27 | `ready-for-agent` | [交付画像切片、披露与反馈闭环](.scratch/bridges-improvement/issues/27-deliver-profile-slices-disclosure-and-feedback-loop.md) | 20, 23, 26 | 最小画像调用及可回放 A 方向闭环 |
| 28 | `ready-for-agent` | [交付原创净室人性化 SKILL](.scratch/bridges-improvement/issues/28-deliver-clean-room-humanizer-skill.md) | 11, 20, 27 | 改写/生成、事实锁、修改说明与许可证清洁 |
| 29 | `ready-for-agent` | [交付生涯规划助手](.scratch/bridges-improvement/issues/29-deliver-career-planning-assistant.md) | 23, 27, 28 | 事实、假设、选择、风险和成长学习路径 |
| 30 | `ready-for-agent` | [交付听写与单条回答朗读](.scratch/bridges-improvement/issues/30-deliver-asr-dictation-and-tts-readaloud.md) | 10, 11, 13 | 固定 ASR/TTS、可编辑转写与按需播放 |
| 31 | `ready-for-agent` | [交付图片生成与编辑](.scratch/bridges-improvement/issues/31-deliver-image-generation-and-editing.md) | 10, 11, 16 | 真实异步生成、资产版本、替代文本与恢复 |
| 32 | `ready-for-agent` | [交付文生视频](.scratch/bridges-improvement/issues/32-deliver-video-generation.md) | 31 | 固定 Wan 任务、取消、恢复和资产管理 |
| 33 | `ready-for-agent` | [交付 QQ SMTP 提醒](.scratch/bridges-improvement/issues/33-deliver-qq-smtp-reminders.md) | 06, 07, 27 | 自发自收验证、日程、画像适配和 24 小时补发 |
| 34 | `ready-for-agent` | [交付 SKILL 插件中心](.scratch/bridges-improvement/issues/34-deliver-skill-plugin-center.md) | 04, 12, 16, 28 | 默认能力、已安装状态和声明式本地上传 |
| 35 | `ready-for-agent` | [交付显式授权 MCP 管理](.scratch/bridges-improvement/issues/35-deliver-permissioned-mcp-plugin-management.md) | 22, 27, 34 | 权限预览、受限运行、再次确认和审计 |
| 36 | `ready-for-agent` | [整合聊天工具、项目和插件选择器](.scratch/bridges-improvement/issues/36-integrate-chat-tools-project-and-plugin-selectors.md) | 13, 16, 19, 22, 28, 29, 34, 35 | 两模式共用完整“+”菜单和三张真实建议卡 |
| 37 | `ready-for-agent` | [交付导出、删除、备份与恢复](.scratch/bridges-improvement/issues/37-deliver-export-delete-backup-restore.md) | 05, 09, 17, 25, 33, 35 | 数据生命周期完整，秘密不进入普通导出 |
| 38 | `ready-for-agent` | [完成电脑端视觉、无障碍与页面状态](.scratch/bridges-improvement/issues/38-complete-desktop-visual-accessibility-and-page-states.md) | 07, 12, 18, 19, 24, 25, 33, 34, 35, 36 | 全页面 Claude-inspired 成品与三档桌面验收 |
| 39 | `ready-for-agent` | [强化安全、隐私与账户隔离](.scratch/bridges-improvement/issues/39-harden-security-privacy-and-account-isolation.md) | 09, 20, 27, 35, 37, 38 | Cookie、跨账户、路径、日志、云披露和扩展攻击测试 |
| 40 | `ready-for-agent` | [建立可复现 A/B 科学评测](.scratch/bridges-improvement/issues/40-build-reproducible-ab-science-evaluation.md) | 23, 27–33, 39 | 画像、人味、低幻觉、风险、多模态和对比报告 |
| 41 | `ready-for-human` | [退出旧产品并通过正式发行门](.scratch/bridges-improvement/issues/41-retire-legacy-and-pass-release-gates.md) | 01, 02, 06, 24, 29–40 | 清除旧壳、空壳与 Stub，双部署从空环境验收 |

## 原始需求反向覆盖矩阵

| Requirement | Issue 落点 |
| --- | --- |
| `UI-01` ChatGPT 电脑端模板 | 04, 12, 13, 38 |
| `UI-02` BridGes 名称与桥梁 Logo | 03, 04, 38 |
| `UI-03` Claude-inspired 风格 | 04, 07, 12, 38 |
| `UI-04` 全模块原创图标 | 04, 13, 36, 38 |
| `UI-05` 登录、注册与全部内容页 | 07, 08, 12, 18, 19, 24, 25, 33–38, 41 |
| `NAV-01` Logo 新聊天 | 12 |
| `NAV-02` 统一搜索 | 24 |
| `NAV-03` 折叠侧边栏 | 12, 38 |
| `NAV-04` 取消“更多” | 12, 41 |
| `NAV-05` 两模式最近对话 | 15 |
| `CHAT-01` 默认新聊天与持续画像 | 11, 13, 26, 27 |
| `CHAT-02` 对话级双模式 | 14, 23 |
| `CHAT-03` 取消临时聊天 | 13, 41 |
| `CHAT-04` 五条学习名言 | 13 |
| `CHAT-05` 无模型选择/实时语音，保留听写发送朗读 | 13, 30 |
| `CHAT-06` 日常陪伴与情境信号 | 14, 26, 27 |
| `CHAT-07` 完整“+”菜单 | 13, 36 |
| `CHAT-08` 三张建议卡 | 13, 36 |
| `CHAT-09` 因材施教与不足自动联网 | 20–23 |
| `CHAT-10` 项目与插件选择 | 19, 34–36 |
| `CHAT-11` 思考摘要 | 14 |
| `KNOW-01` 本地知识库 | 16–18, 20 |
| `PROJ-01` 文件夹式学习项目 | 19 |
| `TASK-01` QQ 邮件提醒 | 33 |
| `EXT-01` MCP/SKILL 安装与展示 | 22, 34, 35 |
| `EXT-02` 默认 PDF/Documents/Humanizer | 28, 34 |
| `EXT-03` 本地上传 SKILL | 34 |
| `PROFILE-01` 九类画像治理 | 25–27 |
| `ACCOUNT-01` 底部账户菜单与账户级密钥 | 08–10 |
| `AUTH-01` 三字段注册 | 07 |
| `AUTH-02` 用户名或 QQ 登录 | 07 |
| `AUTH-03` 登录后设置 | 07–10 |
| `IMP-01` A/B 融合 | 23, 27–29, 40 |
| `IMP-02` 人性化参考与许可 | 28, 34, 40 |
| `IMP-03` 修复空壳、联网和假成功 | 02, 10–13, 18, 21–24, 38–41 |
| `DEPLOY-01` 源码环境与 `BridGes start` | 03, 06, 41 |
| `DEPLOY-02` Docker/Podman | 06, 41 |
| `DEPLOY-03` 无 `.env` | 03, 06, 10, 33, 41 |
| `MODEL-01` 每类固定单模型 | 10, 17, 21, 22, 30–32 |
| `MODEL-02` DuckDuckGo/arXiv | 21–23 |
| `MODEL-03` 同一账户 Key 与真实探测 | 10, 30–32 |
| `DESKTOP-01` 仅电脑端 | 04, 06, 12, 38, 41 |
| `A-01` 画像反馈闭环 | 26, 27, 40 |
| `A-02` 越来越懂用户量化 | 27, 40 |
| `B-01` 四类科学表达的人味验证 | 28, 40 |
| `BONUS-01` 多轮修正 | 27, 40 |
| `BONUS-02` 低幻觉、事实和风险 | 20, 23, 28, 39, 40 |
| `BONUS-03` 与基础/开源方法对比 | 28, 40 |
| `SCORE-01` 科学价值 | 20, 23, 28, 40 |
| `SCORE-02` 技术深度 | 10, 17, 22, 27, 28, 30–35, 40 |
| `SCORE-03` 应用与复现 | 06–41，最终由 41 汇总验收 |

## 推荐执行前沿

用户可先完成 Issue 01；代码代理可立即并行领取 Issue 02。Issue 02 完成后，Issue 03 与 05 可以并行；随后视觉/身份/运行时链路汇入首个聊天纵向切片。每个阶段只认领其 blocker 已解决的最小编号任务，避免多个代理同时修改同一权威模型。
