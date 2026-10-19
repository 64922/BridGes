# 06 — 普通回复逐页读取并保存可恢复证据

**What to build:** 系统能读取首批可见楼层之外的普通回复，每页成功后保留证据，后续页面失败也能交付已有材料。

**Blocked by:** 04 — 打通单话题单帖的真实采集与引用总结

**Status:** ready-for-human

## 实施边界与关键决策

在专用会话中实测普通楼层分页协议，边界校验后转换成领域证据。保存页进度与证据须一致，复用现有节点产物、事务和版本机制；必要的存储变更才引入迁移。

## 验收条件

- [x] 跨至少两页读取，真实帖子/楼层ID、时间单位、吧归属和下一页语义经核对。
- [x] 同页重试和跨页重复按上游稳定身份幂等处理；缺身份不伪造上游ID，不把同文当独立支持数。
- [x] 每页成功后证据可持久读取；第二页失败、worker退出或重复提交不丢第一页面、不重复计数。
- [x] 空页却声明有后继页、重复游标、缺分页信息或计数冲突有有界退出和明确未读原因，不声称全量。
- [x] 第一页外的相关回复可以进入总结并可点击核对；显示实际成功页及部分范围。
- [x] 已实现的DOM补充与结构化证据的身份和读取范围一致；不能用有限滚动替代分页完成判定。

## 验证方式

真实两页协议事实 + 重复/失败页夹具 + 聊天引用与持久化回读。

## 不在本票范围

本票不承担楼中楼分页；不做与逐页证据无关的数据库重构。

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

- 2026-10-19T08:20:21+08:00：开始实现。负责人：opencode；工作树 `D:\BridGes\.worktrees\06-post-reply-pagination`；分支 `06-post-reply-pagination`；基线 `07491043`（main，已含 04 合并）。前置 04 状态为 ready-for-human：代码与离线回归已交付，真实生产模型旅程仍待人工复核；本票先按已合并的 04 实现继续，协议线索以本票真实浏览器样本复核。

- 2026-10-19T09:30:00+08:00：实现与验证完成，转 ready-for-human。
  - 实现：新增 `browser_pagination.py` 纯逻辑（页码 URL、页停止码、逐页合并/去重/上限 240）与 `browser_thread_read.py`（逐页导航、结构化解析、DOM 兜底）；worker 按 `pages_limit` 逐页读取并 emit progress；`receive_result` 在 worker 退出时保留最后一页进度并标 `WORKER_EXITED`；读取投影新增 `pages_complete`、`page_evidence`、`pagination_error_*`，确认帖持久化/校验/总结/展示同步透传。DOM 路径固定 `pages_complete=false` 且 `PAGINATION_UNAVAILABLE`，不冒充分页完成。
  - 真实协议证据：真实帖 `10897336006`（页面声明 10 页/242 回复）成功读取第 1、2 页；第 1 页 14 个顶层帖/28 条含楼中楼，第 2 页 15 个顶层帖/19 条；合并 47 条原始回复，楼层 3–31。结果 `pages_read=2`、`pages_complete=false`、`pagination_error_code=tieba_browser_page_limit`，明确“从第 3 页起未读取”。真实读取发现并修复两点：第 2 页 `.pb-content-wrap` 隐藏导致可见性等待超时（改为 attached）；后续页不重复返回 `first_floor`（改为允许省略，标题仍从 `thread` 读取）。
  - 持久化回读：真实读取结果经 `read_projection` 投影并 JSON 往返后，两页 `page_evidence` 完整保留，投影回复 48 条（含主帖）。
  - 测试：`python -m pytest tests/tieba -q` → 319 passed；`pytest tests/contracts/test_openapi_sync.py tests/tieba -q` → 321 passed；`ruff check src/bridges/tieba tests/tieba` 通过；`mypy src/bridges/tieba` 无本票错误（仍报告 10 个既有外部错误）；前端 `npm run typecheck`、`npm run test:unit`（409 passed）、`npm run lint`（仅既有 img 警告）、`npm run build` 均通过。
  - 全量检查：`pytest -q` 仍因既有 `tests/career_plan` 4 个 `CareerBackgroundLoader` 收集错误中断；`pytest -q --ignore=tests/career_plan` 为 5922 passed / 331 failed / 39 skipped，其中唯一 tieba 失败（缺失 `first_floor` 旧断言）已随协议修正更新，`tests/tieba` 现全绿，其余 330 个失败与本票无关。全仓 `ruff check .` 仍有 456 个既有问题，未指向本票文件。
  - 契约与版本：浏览器协议 `tieba-browser-protocol-v2`、recipe `tieba-recipe-v8`、读取能力 `tieba-read-v9`；新增 `TiebaPageEvidence` 与公开分页字段；已重新生成 `openapi.json` 与 `packages/contracts/src/generated.ts`。无数据库 schema 迁移。
  - 回滚：回退本票提交即可；worker 协议 v2、recipe v8 与读取能力 v9 必须成组回退，旧 worker 不兼容 v2 进度/分页字段。
  - 未验证/剩余：真实生产模型总结与引用未在本票重新跑通（04 的真实模型旅程仍待人工；本票用端到端测试验证第 2 页引用进入总结及持久化回读）；真实浏览器临时 profile 已删除。07 的普通页分页代码依赖已满足。
