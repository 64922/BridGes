# 10 — 停止、登录等待和故障后从有效证据续接

**What to build:** 用户停止后没有新的后台请求；等待登录、进程中断或暂时失败后可从有效进度恢复，改了话题不会收到旧任务结果。

**Blocked by:** 09 — 五分钟内优先覆盖多帖并准时收尾

**Status:** ready-for-human

## 实施边界与关键决策

贯通现有任务版本、租约、逐页证据、预算快照与恢复动作。自动续接属于原任务累计预算；用户显式新重试必须显示新运行及其复用范围，不能暗中重置同一轮预算。

## 验收条件

- [ ] 排队、搜索、普通分页、楼中楼、模型等待及提交前停止均受守卫约束；清理后占位和资源有界释放。
- [ ] 认证等待结束自动续接当前有效任务；窗口关闭后通过明确继续恢复，已停止任务不会自行复活。
- [ ] 刷新、重开聊天、worker异常和服务重启后，成功页与进度一致；恢复不重读已确认页或重复计量已保存证据。
- [x] 条件改变、账户改变、租约过期和版本升级时旧任务不能继续提交；不兼容产物不得复用。
- [ ] 自动恢复累计工作时间不归零；人工等待只扣除一次；耗尽任务没有无限自动续跑。
- [x] 模型失败后保留证据，合法重试优先只重做失败阶段；若只能整轮重跑，界面明确范围，不伪装为局部重试。
- [x] 所有恢复入口来自服务端真实能力，按钮、自然语言继续、历史重开语义一致。

## 验证方式

每阶段故障注入 + 刷新/重启/条件变更的应用级回归 + 一次真实登录等待续接。

## 不在本票范围

不另建恢复引擎，不引入双写或长期兼容shim；历史结果继续只读。

## 通用执行与交接要求

- 执行前阅读本工作流执行方案及领域合同；复核当前实现，已有通过能力优先复用，不能依据历史问题描述重建已完成模块。
- 使用 conda agent 环境。先建立能捕获本票缺陷/行为的测试，再实现；每票包括所需公开投影、中文界面状态和端到端验证，不以单层完成替代行为交付。
- 外部协议须由真实响应验证；若主路径不能满足合同，说明验证过的备选与剩余差距，不静默缩减范围。不得搬运参考项目源码或引入其业务客户端。
- 共享预算、账户、任务版本、租约与证据边界保持一致；内部旧路径直接移除，不新增长期双实现。稳定API及历史数据按外部合同评估。
- 提交前执行项目完整检查，前端变更额外构建，协议变更同步生成；schema/存量数据变化才执行对应迁移。变更源码及测试单文件不超过500行，必要拆分只限本票职责。
- 交接记录实际行为、测试命令及退出码、真实/模拟证据、未验证项、回滚方法、公开契约与版本变化。完成实现但待人工复核可转 ready-for-human；只有验收证据合格才解除下游阻塞。

## Comments

- 2026-10-18：依据当前代码、既有ADR和本次运行账本故障分析形成待确认草案；未执行开发。


- 2026-10-18：用户确认拆分，正式发布为 ready-for-agent；编号、内容范围和依赖保持已批准方案，未开始业务开发。

- 2026-10-19：实现与受控验证完成，转 `ready-for-human`。前置 09 当前仍为 `ready-for-human`（实现已合并 main 的 `f81ec2df`），本票未把 09 当作人工验收通过；13/14 的阻塞不因本票状态自动解除。
  - 工作树 `D:\BridGes\.worktrees\10-stop-resume-recovery`；分支 `10-stop-resume-recovery`；基线 `8ad71b1c`（main）；本票提交 `52047b41`，已合并 main 至 `b10df566`，合并后 `tests/tieba` 重跑 `418 passed`。
  - 检查点与恢复：搜索每轮、每次已确认帖子读取后写 `partial=True` 产物（只写产物，不写完成收据，提交仍由 `RunCommitGuard.verify()` 守卫）。恢复先找 input_key 精确 partial，再按 parse/plan 内容哈希扫描历史 partial；校验任务、配方、能力、schema、产物类型、partial/reusable/hash 后才复用。不兼容产物拒绝恢复。
  - 任务绑定：parse input_key 纳入任务版本；pending 登录等待保存 `task_id`/`task_version`，任务变化时旧等待清除且旧任务不能提交。自然语言“继续”在没有显式续接问题时使用当前任务主题词，避免把“继续”当作检索主题；已有显式问题仍优先。
  - 停止与总结：停止事件传入总结批次、聚合和核验前检查；模型失败或未完整总结时 synthesize payload 标 `partial=True`，kernel 不复用 partial 产物；合法重试优先复用搜索/读取证据，只重做总结与核验。服务端投影新增 `recovery`（动作、范围说明、追问），认证等待、停止、失败均由真实能力生成。
  - 界面：`TiebaAnswer` 使用服务端 `recovery.scope_note` 展示认证等待/停止/局部重试范围；`AssistantActions` 的 retry 文案按能力显示“重试未完成阶段”/“重新运行”/“重新生成”；覆盖历史重开与局部重试测试。
  - 公开契约与版本：`TiebaRecovery`/`TiebaRecoveryAction` 及 `recovery` 字段已同步 `openapi.json`、`packages/contracts/src/generated.ts`；recipe 升为 `tieba-recipe-v12`，搜索 `tieba-search-v12`、读取 `tieba-read-v13`、总结 `tieba-synthesize-v12`。无数据库 schema 或存量数据迁移。
  - 测试（退出码均为 0）：`python -m pytest tests/tieba -q` → `418 passed`；`python -m pytest tests/contracts -q` → `3 passed`。新增 `tests/tieba/test_stop_resume_checkpoint.py`（2 个检查点/崩溃测试）、`tests/tieba/test_stop_resume_recovery.py`（7 个恢复/重试/任务变化测试），共享 helper 在 `tests/tieba/stop_resume_support.py`。模型失败重试测试用新的 `TiebaResearchService` 实例模拟服务重启后复用已保存证据。
  - 其他检查：变更 Python 文件 `ruff check` 通过；`mypy src/bridges/tieba` 仅 10 个既有跨模块错误，tieba 无错误；`tests/chat tests/kernel` 为 `1194 passed / 94 failed`，失败均属既有 retired 模块等无关路径，未发现贴吧回归。前端 `npm run lint` 通过（1 个既有 `<img>` warning）、`npm run typecheck` 通过、`npm run test:unit` 55 文件/422 测试通过、`npm run build` 成功（worktree junction 触发 standalone 符号链接 EPERM 警告，构建仍成功）。
  - 真实/模拟证据：使用可控时钟、检查点崩溃注入、应用级 HTTP 登录等待换话题、新服务实例重启、模型失败与旧任务版本变更验证。**未执行真实登录等待续接，也未真实杀死 worker/进程或重启后端服务**；真实上游登录态、限流与清理耗时仍需人工复核。
  - 未验证/剩余：真实登录窗口关闭后继续；真实 worker/进程异常与服务重启；真实上游限流和清理资源测量；全仓既有失败基线未重新归零。
  - 回滚：回退本票提交即可；recipe 与搜索/读取/总结版本须成组回退，避免旧代码读取新版本产物。历史投影缺少 `recovery` 时前端按空值读取，无需数据迁移。下游 13/14 继续等待人工验收本票真实登录与重启场景。
