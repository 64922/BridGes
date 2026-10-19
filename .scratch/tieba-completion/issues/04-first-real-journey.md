# 04 — 打通单话题单帖的真实采集与引用总结

**What to build:** 用户在应用内提出一个明确话题，系统从真实吧内候选读取至少一条相关原文，经生产模型生成有可核对引用的短总结。

**Blocked by:** 01 — 页面并发加载不再误报预算耗尽；02 — 自然语言话题生成有效且不重复的检索计划；03 — 专用会话认证成功后自动续接原话题

**Status:** ready-for-human

## 实施边界与关键决策

沿现有内核、专用浏览器、证据转换、模型网关与正文投影串通最小纵向旅程。实测选定搜索与帖子读取的结构化响应，DOM仅为补充；修复这条最小链路暴露的接缝，不在此实现完整分页。形成后续分页可复用的协议事实与脱敏夹具。

## 验收条件

- [ ] 从正式聊天入口运行，不以直接调用解析器、个人浏览器DOM或假模型演示替代。
- [ ] 候选经真实帖子内容确认目标吧和话题；搜索摘要不能直接进入总结证据。
- [ ] 取得真实帖子身份、原文、可取得的发言身份和时间；缺失字段保持未知，响应校验失败有明确状态。
- [ ] 真实模型经过已有网关及预算生成总结和支持核验；关键引用在界面能打开实际采集原文与原帖。
- [x] 记录运行标识、版本、请求与模型调用计数、耗时、可核对来源及未读范围；不记录会话秘密。
- [x] 最小失败旅程覆盖访问限制、读取为空、模型失败，均不伪造成功；成功旅程及脱敏夹具可复验（成功旅程的真实模型/真实浏览器部分未在本轮执行，见交接）。

## 验证方式

先验证真实协议，再把脱敏结构固定为回归；最后执行最小生产旅程。明确生产模型调用次数与费用范围，遇实际权限门时说明所需范围。

## 不在本票范围

不承诺全量回复或全吧覆盖；不引入参考项目业务客户端。

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

- 2026-10-19T07:57:32+08:00：实现交接（worktree `D:\BridGes\.worktrees\04-first-real-journey`，分支 `04-first-real-journey`，基线 `57628053`）。
  - 已完成：搜索/帖子结构化响应解析与 DOM 兜底；主帖/回复 `author`、`origin_reply_id`、DOM 楼层补充；`response_valid`、`total_pages`、未读范围；引用作者核验；`TiebaRunMetadata`（运行标识、配方/能力版本、请求与模型调用计数、耗时、来源、未读范围）；引用与运行记录 UI；OpenAPI/TS 契约同步。
  - 协议事实：搜索响应 `/mo/q/search/thread`，帖子响应 `/c/f/pb/page_pc`；`page_pc.post_list[].floor` 恒为 0，真实楼层由 DOM 补充；缺失字段保持 `None`，结构不符映射 `tieba_browser_response_invalid` / `ReadStatus.UNRECOGNIZED`。
  - 测试：`python -m pytest tests/tieba -q` → 296 passed；`python -m pytest tests/tieba/test_run_metadata.py tests/tieba/test_summary_source_identity.py -q` → 6 passed；`python -m pytest tests/tieba/test_service_summary_audit.py -q` → 3 passed；`npm run test:unit` → 408 passed；`npm run typecheck`、`npm run lint`、`npm run build` 通过；`pip check` 通过；`ruff check src/bridges/tieba tests/tieba` 通过。
  - 全量基线：`pytest -q` 在收集阶段因既有 `CareerBackgroundLoader` 缺失报 4 个错误（与本次改动无关）；忽略 `tests/career_plan` 后 5900 passed / 330 failed，失败均为既有其它模块基线，未出现 `tests/tieba` 失败。全仓 Ruff 457 个既有错误、`mypy src` 141 个既有错误，均未指向本次新增/修改的贴吧文件。
  - 真实旅程：已准备临时库、真实账号与复制后的专用浏览器 profile，并打开正式 Web 登录页；按用户要求跳过登录/采集/模型调用。**本轮未发出生产模型调用，未取得真实成功旅程、UI 引用点击与费用证据。** 临时账号、数据库、复制的浏览器 profile（含登录态）与临时进程均已删除。
  - 完成剩余验收所需：使用已登录的专用贴吧会话从正式聊天发送明确话题；确认至少一条真实原文与作者；让真实模型经网关生成总结；核对 `run_metadata.model_call_count` 与实际模型锁/请求账本一致并记录调用次数与费用范围；在 UI 打开引用核验原文和原帖。
  - 公开契约：新增 `TiebaRunMetadata` 并出现在 `TiebaResearchProjection.run_metadata`；`TiebaReply.author`、`TiebaSummaryCitation.author`。无数据库 schema 迁移。浏览器协议版本 `tieba-browser-protocol-v1`。
  - 回滚：本票尚未提交；可丢弃工作区改动或回退对应提交。新增文件 `browser_structured.py`、`browser_capture.py`、`browser_scroll.py`、`run_metadata.py` 可整体删除，`openapi.json` 与 `packages/contracts/src/generated.ts` 重新生成。
