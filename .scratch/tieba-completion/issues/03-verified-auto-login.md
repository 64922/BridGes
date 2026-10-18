# 03 — 专用会话认证成功后自动续接原话题

**What to build:** 明确未登录时自动打开官方登录页；用户完成登录后，系统独立确认认证和采集能力，自动继续有效任务，无需再发送话题。

**Blocked by:** 01 — 页面并发加载不再误报预算耗尽

**Status:** ready-for-human

## 实施边界与关键决策

先在应用账户对应的专用会话实测可证明已认证的响应，记录字段含义与失败分类，再实现认证状态机。认证与采集可用性分离；复用现有账户锁、登录等待和任务守卫。登录响应只转换为最小状态，不向模型或公开记录传播凭据。

## 验收条件

- [x] 真实验证匿名、已认证两种状态；目录、Cookie存在、公开DOM可读、tbs存在都不能单独证明登录。真实匿名/已认证探测与公开 DOM 对照已完成。
- [ ] 在聊天中完成“发起→自动打开官方入口→用户登录→认证复验→采集复验→自动续接”，无额外完成按钮和正常流程下的“继续”。状态机、复验、自动续接已由受控测试覆盖；真实登录过渡需人工复验。
- [x] 已认证但需验证时进入对应官方验证入口；网络失败、状态未知不伪装为未登录，不反复弹窗。安全验证场景为受控测试，真实验证码未实测。
- [x] 同账户并发等待只打开一个入口，任务身份分别保留；不同账户不复用会话。复用现有账户锁与 profile 哈希隔离；并发/隔离回归通过。
- [x] 关闭窗口保留任务并给出真实恢复方式；停止或条件变化后，即使登录成功也不自动执行旧任务。
- [x] 认证等待开始/结束有配对事件，人工等待独立计时；状态缺失或重复事件不重复延长预算。
- [x] 有真实专用会话的成功证据；不可获得的安全验证场景用受控测试覆盖并明确未实测，不能编造认证接口可用性。真实专用会话检索 8 条（首次 5 条）、读取 13 条回复。

## 验证方式

状态机/并发夹具 + 一次真实专用登录与自动续接；外部验证条件不足时准确交接阻塞，不标已完成。

## 不在本票范围

不读取或搬运个人浏览器会话，不绕过验证码；不把历史单次模型验收授权当作新的调用额度。

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
- 2026-10-18T21:13+08:00：认领本票。工作树 `D:\BridGes\.worktrees\03-verified-auto-login`，分支 `03-verified-auto-login`，基线 `2884306d`（最新 main）。前置 01 的实现与交接已在 main（`4dfa432e`、`3e6bf90c`、`2884306d`），状态为 `ready-for-human`；其交接记录显示自动与本地浏览器夹具已通过，真实专用会话最小旅程留给 04 复验，01 尚未取得人工验收结论。按用户授权在本票同时复核该实现的接口行为，但不代替 01 的人工验收。

- 2026-10-18T21:44+08:00：实现并交接，转 `ready-for-human`。
  - 认证合同：只以 `https://tieba.baidu.com/dc/common/tbs` 的 `is_login` 确认真实登录；网络/结构异常返回 `auth_status_unknown`，不打开登录页。目录、Cookie、公开 DOM、tbs 是否存在均不单独作为登录证据。
  - 状态机：`AuthSession` 在动作前探测；匿名时复用当前页打开官方登录页并等待，离开 passport 后复验；已登录仍被验证码拦截时留在官方验证页，清除后复验并重试原动作一次；重复登录/验证入口不再弹窗，抛 `auth_interrupted`。正常流程在同一个 worker 请求内自动续接，不返回“继续”。
  - 公开错误码：新增 `tieba_browser_auth_status_unknown`、`tieba_browser_collection_restricted`，均终止且不回退普通公开搜索；`auth_required`、`auth_interrupted` 仍为可恢复等待；历史 `auth_unverified` 仅保留错误码兼容读取，不再由新路径产生。
  - 等待预算：worker 发送配对 `auth_wait started/finished`；runtime 以 `AuthWaitTracker` 只计一次，调用 `TiebaBudget.record_manual_wait`，账本 `record_manual_auth_wait` 延长截止且不增加外部调用计数；停止/任务变化/超时/结果/错误均只结束一次。worker 同时把扩展后的截止同步给请求跟踪器。
  - 其他：`search` 的 deadline 保留调用方兜底；`browser_request_budget` 在守卫/停止变化时拒绝并拒绝已排队请求，避免 worker 等待决策；`_auth_wait_question` 使用真实错误消息并给出“继续”恢复方式，`presenting` 避免重复提示。
  - 测试命令与结果（conda `agent`，worktree）：
    - `python -m pytest tests/tieba -q` → `271 passed`。
    - `python -m pytest tests/tieba tests/chat/test_improvement09_budget_batches.py tests/chat/test_improvement09_run_budget_integration.py tests/chat/test_improvement09_run_budget_ledger.py tests/chat/test_improvement09_run_budget_ledger_lifecycle.py tests/chat/test_improvement09_run_budget_ledger_operations.py -q` → `336 passed`。
    - 定向 ruff（全部本票源码/测试）→ `All checks passed!`；`ruff check .` 仍有 456 个既有全仓错误，未出现在本票变更文件。
    - 定向 mypy（本票源码）→ 仅报告 10 个既有跨模块错误，本票文件无错误。
    - `python -m pytest tests/chat -q` → `1173 passed, 94 failed`；抽查 `test_mcp_call_chat.py::test_selected_mcp_call_succeeds`、`test_selections.py::test_builtin_skill_selection_persists_on_create` 在 main 同样失败，原因是用户扩展/旧能力已退役，与 03 无关。
  - 真实证据（专用 profile `...\tieba-browser\5a214...b649a1`，未读取/导出 Cookie）：
    - 认证探测 `auth_state=authenticated`；匿名对照实测 `is_login=0` 且公开搜索 20 条、无登录引导，证明公开 DOM 不能证明登录。
    - 新代码真实 search `宿舍 热水` → 8 条结果（复跑；首次 5 条），`excluded_wait_ms=0`，示例标题「求助询问宿舍环境」。
    - 新代码真实 read `p/10223513196` → 标题「询问宿舍环境」、吧名「华东交通大学吧」、13 条回复、首楼 floor 2。
  - 未实测/人工复验：真实“匿名→用户登录→自动续接”完整旅程；真实安全验证码出现/清除；真实关闭登录窗口后的继续。受控测试已覆盖状态机，但不得据此宣称这些真实场景已通过。真实专用会话当前已是已登录状态，未为测试主动退出或破坏会话。
  - 回滚：撤销本票合并提交即可恢复旧 `official_login` 与新错误码前的行为；本票无 schema/迁移变更，错误码为新增，历史 `auth_unverified` 仍可读取。
  - 下游阻塞：本票仍为 `ready-for-human`；人工完成真实登录过渡/验证场景复核前，不解除下游阻塞。
