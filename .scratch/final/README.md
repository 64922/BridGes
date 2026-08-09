# BridGes 最终改进 Issue 执行索引

Status: approved

本目录是本轮产品改进的唯一执行入口。需求基线来自用户提供的《后端问题》和《前端问题》、配套截图、二十三项已确认产品决策，以及对当前代码、测试、领域文档和参考项目的只读审查。

旧的 bridges-improvement 任务和历史计划继续作为实现背景保留，但与本目录冲突时，以本目录的 PRD、Issue 01 产出的新 ADR 和更新后的领域词汇为准。

需求到 Issue 的完整映射见 [TRACEABILITY.md](TRACEABILITY.md)，最终兼容收缩的运行证据模板见 [COMPATIBILITY-GATE.md](COMPATIBILITY-GATE.md)。

## 代码代理执行规则

1. 只领取 Status 为 ready-for-agent 且所有 Blocked by 均已交付的 Issue。
2. 每个 Issue 必须独立保持主分支可构建、可迁移、可回滚；不得依靠后续 Issue 修复本 Issue 主动造成的红灯。
3. 修改公开接口时，同一 Issue 内同步更新 OpenAPI、生成类型、客户端和合同测试。
4. 修改数据库或本地对象时，同一 Issue 内提供幂等迁移、重复运行测试、备份恢复验证和失败回滚证据。
5. 替换旧行为时，重写锁定旧需求的测试；不得通过跳过、放宽断言或只隐藏界面制造“通过”。
6. 所有用户可见文字、错误和无障碍标签使用中文；确定性错误、引用、工具状态和安全提示不得被人味化改写。
7. 每个 Issue 完成后，将验证命令、结果摘要、迁移证据和遗留风险追加到该文件的 Comments，并把状态改为 ready-for-human。
8. 不顺手重构无关模块；每条改动必须能追溯到当前 Issue 的验收标准。
9. Issue 24 除满足 Blocked by 外，还必须等待 `COMPATIBILITY-GATE.md` 状态为 `passed`；没有完整迁移版本与真实旧调用清零证据时不得领取最终收缩。

## 推荐执行波次

- 波次 0：01
- 波次 1（可并行）：02、03、04、05、14
- 波次 2（可并行）：06、11、15
- 波次 3（可并行）：07、08、09、10、12、16
- 波次 4（可并行）：13、17、20
- 波次 5（可并行）：18、21、22、23
- 波次 6：19
- 波次 7：24

波次只是推荐并行方式，实际领取仍以每个 Issue 的 Blocked by 为准。

Issue 07–10 可并行实现业务适配器，但都会扩展 Issue 06 的同一路由注册表；代理必须采用可加性注册并运行共享互斥矩阵，合并时不得以覆盖枚举、提示或分支顺序解决冲突。

Issue 21 与 22 可并行交付新聊天页和既有会话页，但可能共用输入组件；两者必须保持同一最小消息合同，以页面变体或清晰属性隔离布局，禁止互相恢复已退役控件。合并后运行两个页面的联合回归。

## Issue 清单

| Issue | 标题 | 状态 |
| --- | --- | --- |
| [01](issues/01-freeze-product-contracts-and-migration-gates.md) | 冻结新版产品契约并建立迁移门 | ready-for-agent |
| [02](issues/02-migrate-learning-projects-to-global-knowledge-base.md) | 将旧学习项目资料迁入全局知识库 | ready-for-agent |
| [03](issues/03-retire-task-scheduling-and-review-reminders.md) | 安全退役任务安排、邮件提醒与旧复习调度 | ready-for-agent |
| [04](issues/04-retire-user-skills-and-mcp-management.md) | 隔离并退役用户 SKILL/MCP 管理 | ready-for-agent |
| [05](issues/05-lock-conversation-mode-on-first-turn.md) | 在首条消息提交时永久锁定对话模式 | ready-for-agent |
| [06](issues/06-route-natural-language-paper-search.md) | 以论文搜索打通持久化自然语言能力路由 | ready-for-agent |
| [07](issues/07-route-natural-language-humanizer.md) | 自然语言触发高质量文章人味化 | ready-for-agent |
| [08](issues/08-route-natural-language-image-capabilities.md) | 自然语言触发图片生成与知识库图片编辑 | ready-for-agent |
| [09](issues/09-route-natural-language-video-generation.md) | 自然语言触发视频生成 | ready-for-agent |
| [10](issues/10-route-natural-language-career-planning.md) | 自然语言触发生涯规划 | ready-for-agent |
| [11](issues/11-make-global-knowledge-base-the-only-file-source.md) | 让知识库成为唯一文件上传与本地检索来源 | ready-for-agent |
| [12](issues/12-decide-retrieval-by-intent-and-simplify-citations.md) | 按能力与意图决定是否检索并精简引用呈现 | ready-for-agent |
| [13](issues/13-harden-automatic-online-evidence-search.md) | 自动联网补足学习证据并验证真实搜索能力 | ready-for-agent |
| [14](issues/14-expand-four-dimension-profile-and-migrate.md) | 扩展四维画像模型并迁移旧九维画像 | ready-for-agent |
| [15](issues/15-activate-automatic-profile-extraction-and-injection.md) | 交付每消息自动画像、故障重试与最小画像注入 | ready-for-agent |
| [16](issues/16-contract-legacy-profile-governance.md) | 收缩旧画像许可、通知、候选与复杂治理界面 | ready-for-agent |
| [17](issues/17-apply-global-humanized-writing-policy.md) | 为所有自然语言回答注入轻量有人味表达策略 | ready-for-agent |
| [18](issues/18-create-personalized-teaching-plan-and-first-lesson.md) | 创建画像与证据驱动的学习计划并交付第一课时 | ready-for-agent |
| [19](issues/19-persist-lessons-and-adaptive-quizzes.md) | 持久推进课时、插入自适应测验并保证重试幂等 | ready-for-agent |
| [20](issues/20-contract-sidebar-and-retired-routes.md) | 收缩侧栏、退役旧页面并扩展最近对话空间 | ready-for-agent |
| [21](issues/21-simplify-new-chat-home.md) | 精简新聊天首页并将模式选择移至右上角 | ready-for-agent |
| [22](issues/22-fix-conversation-viewport-and-remove-manual-controls.md) | 固定对话输入框并移除会话内模式及手动工具控件 | ready-for-agent |
| [23](issues/23-fix-recent-conversation-menu-positioning.md) | 修复最近对话操作菜单的裁剪与碰撞定位 | ready-for-agent |
| [24](issues/24-contract-legacy-apis-and-run-release-regression.md) | 完成旧契约收缩、类型再生成与全量发布回归 | ready-for-agent |

## 全局完成定义

- 两种对话模式只在新聊天发送前可选，首条消息提交后服务端永久锁定。
- 用户只需发送自然语言即可调用论文搜索、人味化、图片、视频和生涯规划能力。
- 文件和图片只从知识库页面上传；聊天不再存在附件、项目、插件或手动工具选择。
- 检索只在任务需要时执行；学习证据不足会自动联网，成功结果有引用，失败不伪造。
- 画像只展示四类自动记录，用户可修改和撤回；每轮回答使用最小相关画像。
- 普通自然语言回答应用轻量有人味策略；显式文章改写执行完整事实锁和质量检查。
- 学习模式形成持久化个性计划，每个有效教学回复最多推进一个课时并按节奏插入测验。
- 侧栏、首页、画像页和对话页符合新截图意见；最近会话菜单在各桌面视口及 200% 缩放下完整可见。
- 全量后端、前端、合同、安全、迁移、无障碍、视觉和真实联网冒烟验证通过。
