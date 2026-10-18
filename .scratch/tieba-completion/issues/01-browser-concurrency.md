# 01 — 页面并发加载不再误报预算耗尽

**What to build:** 用户发起贴吧搜集时，正常页面产生多个请求可以受控排队完成；只有真正时间耗尽才结束本轮，失败信息与真实原因一致。

**Blocked by:** 无 — 可立即开始

**Status:** ready-for-human

## 实施边界与关键决策

先用真实预算账本和浏览器事件复现第三个并发请求被错误拒绝。将预算许可的原因与暂时容量不足分开，在浏览器适配边界建立有界、可取消的请求调度。只为这条链路做必要的内部接口调整，逐一迁移受影响调用方，不做全仓预算重构。排队、完成事件和 IPC 持续推进，禁止在同步路由回调中阻塞完成事件。

## 验收条件

- [ ] 剩余约255秒、并发上限2、同时触发至少3个数据请求的回归先红后绿；最多2个在途，其余等待，完成后继续，不关闭整个浏览器操作。
- [ ] 页面导航、XHR、fetch 经同一账本登记和一次性释放；队列长度和等待均有界，worker退出、请求失败、取消、超时后无泄漏和负占位。
- [ ] 明确区分并发等待、工作时间耗尽、运行失效、取消和守卫失败；拒绝记录含脱敏原因、当前占位、上限及剩余时间。
- [ ] 真实预算耗尽后不再切换同义词重复请求；暂时容量不足由请求调度处理，不消耗新的查询轮次。
- [ ] 用本地浏览器响应夹具证明路由等待不会死锁，并验证已有搜索请求成功时不再声称整轮未发请求。
- [ ] 聊天入口能显示等待或真实失败状态，相关共享账本调用方回归通过。

## 验证方式

内存账本最小复现 → 本地浏览器并发响应夹具 → 聊天入口错误投影；真实专用会话最小旅程由04复验。

## 不在本票范围

不通过简单调大并发、延长五分钟、关闭守卫或绕过账本掩盖缺陷。

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
- 2026-10-18：实现完成并转 ready-for-human。行为：浏览器请求许可区分容量等待、工作时间耗尽、动作超时、运行失效、取消、守卫失败与队列满；最多 2 个在途，其余进入有界队列，完成后由同一账本释放并继续；真实工作时间耗尽/动作超时/取消/守卫失败/队列满才终止本轮并写脱敏拒绝记录（原因、当前占位、上限、剩余工作时间、稳定拒绝码）。修复前第三个并发请求在剩余约 255 秒时被误报预算耗尽；修复后同一场景排队完成。动作截止不再伪装成工作时间耗尽，改报 `tieba_browser_timeout`。`tieba_browser_work_time_exhausted` 与 `tieba_browser_request_queue_full` 不再触发公开搜索回退或同义词轮次。提交：`4dfa432e`（主实现）、`3e6bf90c`（超时区分与评审修正），分支 `01-browser-concurrency` 已同步 main 最新 `a36df883`。
  验证命令与结果：红测 `python -m pytest tests/tieba/test_issue01_browser_concurrency.py -q -o pythonpath="D:\BridGes\src"` → 5 failed, 1 passed（基线无 deadline/调度接口）；绿测 `python -m pytest tests/tieba -q` → 256 passed；共享账本回归 `python -m pytest tests/chat/test_improvement09_*.py tests/chat/test_run_budget_external_results.py tests/storage/test_schema_v63.py tests/lifecycle/test_run_budget_lifecycle.py -q` → 67 passed；`ruff check` 本票改动文件 → All checks passed；`pip check` → No broken requirements found；本票文件 `mypy` → 无新增错误（全仓 `mypy src` 仍有 10 个既有无关错误）。本地浏览器夹具使用系统 Chrome + 本地 HTTP 服务 + 真实 SQLite 账本：3 个并发 fetch 峰值 2、排队 1、无死锁、4 次外部调用与 4 次结果、0 拒绝、无泄漏。真实/模拟边界：确定性内存复现与本地真实浏览器夹具已过；真实贴吧专用会话按本票验证方式留给 04。
  未验证/已知差距：真实贴吧会话最小旅程由 04 复验；容量等待是动作内瞬态，未新增独立聊天等待文案（认证等待仍沿用既有 `waiting_auth` 投影，`tests/tieba/test_browser_api.py` 覆盖）；全仓 `pytest -q` 在收集期被既有 `tests/career_plan` 的 `CareerBackgroundLoader` 缺失阻断，`--ignore=tests/career_plan -x` 的首个失败为既有 `test_production_wiring_injects_recorder`，均与本票无关；全仓 ruff 仍有 456 个既有错误。
  公开契约/版本变化：`SCHEMA_VERSION` 89→90；迁移 90 重建 `run_budget_entries` 的 kind CHECK，新增 `external_call_denied`（不改变预算计数或运行状态），并保留全部行与索引；升级前自动备份。错误码新增/明确 `tieba_browser_work_time_exhausted`、`tieba_browser_request_queue_full`、`tieba_browser_run_invalidated`、`tieba_browser_request_guard_failed`、`tieba_browser_timeout`，移除 `tieba_browser_run_budget_exhausted`。回滚：代码回退到本票两个提交并恢复升级前备份；v90 数据库不能由旧二进制直接打开（版本守卫会拒绝）。
